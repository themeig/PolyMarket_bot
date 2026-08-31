"""
=============================================================================
POLYMARKET FULL REAL-TIME PLATFORM: MARKET MAKING & LOGICAL SPREAD ARBITRAGE
=============================================================================
"""

import sys
import os
import asyncio
import aiohttp
from aiohttp import web
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from web3 import Web3

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client_v2.order_builder.constants import BUY, SELL
from step2_smart_screener import get_all_active_markets, filter_dual_engine_markets, format_iso_time_ago, is_sports_market
from step3_logical_screener import fetch_all_logical_opportunities
from avellaneda_stoikov import AvellanedaStoikovEngine, ASQuoteResult
from token_merger import TokenMerger
from ai_trainer import trainer
from live_simulator import sim_engine


if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROXY_WALLET = "0x117da19a541ba6d89ae52043a67dbc22572d1de8"

def format_timestamp_ago(ts):
    if not ts:
        return "N/D"
    try:
        now = time.time()
        diff = now - float(ts)
        if diff < 0:
            return "Adesso"
        if diff < 60:
            return f"{int(diff)}s fa"
        elif diff < 3600:
            mins = int(diff // 60)
            return f"{mins}m fa"
        elif diff < 86400:
            hours = int(diff // 3600)
            return f"{hours}h fa"
        else:
            days = int(diff // 86400)
            return f"{days}g fa"
    except Exception:
        return "N/D"

class CompletePolymarketQuantBot:
    def __init__(self):
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
        self.private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
        if not self.private_key.startswith("0x") and len(self.private_key) == 64:
            self.private_key = "0x" + self.private_key

        self.rpc_url = os.getenv("RPC_URL", "https://polygon.drpc.org")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.account = self.w3.eth.account.from_key(self.private_key)
        self.wallet_address = self.account.address
        self.proxy_wallet = PROXY_WALLET

        self.client = ClobClient(
            host="https://clob.polymarket.com", 
            key=self.private_key, 
            chain_id=137,
            signature_type=3,
            funder=self.proxy_wallet
        )
        self.api_creds = self.client.create_or_derive_api_key()
        self.client.set_api_creds(self.api_creds)

        self.initial_usdc = None
        self.free_usdc = 12.55
        self.pol_gas = self.fetch_onchain_pol()

        # =========================================================================
        # PARAMETRI DINAMICI & FILTRI
        # =========================================================================
        self.enable_hft = True
        self.enable_wide = True
        self.enable_as_mm = True          # Motore Avellaneda-Stoikov Dual-Bidding
        self.enable_auto_merge = True     # Complete Set Merging on-chain
        self.as_gamma = 0.15              # Risk Aversion
        self.exclude_sports = True        # Filtro Sport attivo di default
        self.max_total_open_orders = 4
        self.max_order_spend = 1.60
        self.take_profit_pct = 15.0
        self.stop_loss_pct = -18.0

        self.as_engine = AvellanedaStoikovEngine(
            gamma=self.as_gamma,
            delta_min_ticks=2,
            c_vol=1.5,
            q_max_usdc=12.0,
            base_size_usdc=self.max_order_spend
        )
        self.token_merger = TokenMerger(
            private_key=self.private_key,
            proxy_wallet=self.proxy_wallet,
            rpc_url=self.rpc_url
        )
        self.last_as_quotes = {}
        self.mergeable_pairs = []
        self.last_heartbeat_time = 0

        self.killswitch_loss_limit = 2.50

        self.running = True
        self.killswitch_triggered = False
        self.active_real_orders = []
        self.trade_history = []
        self.rewards_screener = []
        self.hft_screener = []
        self.wide_screener = []
        self.current_screener = []
        self.logical_opportunities = []
        self.last_scan_time = 0
        self.last_logical_scan_time = 0
        self.market_names_cache = {}

    def fetch_onchain_pol(self):
        now = time.time()
        if hasattr(self, "_cached_pol") and (now - getattr(self, "_cached_pol_ts", 0)) < 60.0:
            return self._cached_pol
        try:
            wei = self.w3.eth.get_balance(self.wallet_address)
            self._cached_pol = float(self.w3.from_wei(wei, 'ether'))
            self._cached_pol_ts = now
            return self._cached_pol
        except Exception:
            return getattr(self, "_cached_pol", 0.0)

    def get_clob_collateral(self):
        now = time.time()
        if hasattr(self, "_cached_cash") and (now - getattr(self, "_cached_cash_ts", 0)) < 8.0:
            return self._cached_cash
        try:
            p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = self.client.get_balance_allowance(p)
            self._cached_cash = float(bal.get("balance", 0) or 0) / 1e6
            self._cached_cash_ts = now
            return self._cached_cash
        except Exception:
            return getattr(self, "_cached_cash", 1.83)

    async def trading_loop(self):
        print(f"[+] Motore Ibrido (Market Making & Spread Logico) avviato...")
        while True:
            try:
                if self.running and not self.killswitch_triggered:
                    await self.execute_trading_cycle()
            except Exception as e:
                print(f"[!] Errore ciclo: {e}")
            await asyncio.sleep(2.0)

    async def execute_trading_cycle(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.pol_gas = self.fetch_onchain_pol()

        try:
            open_orders = self.client.get_open_orders()
            self.live_open_orders_cached = open_orders
            open_sell_assets = set([str(o.get("asset_id")) for o in open_orders if o.get("side") == "SELL"])
            open_buy_assets = set([str(o.get("asset_id")) for o in open_orders if o.get("side") == "BUY"])
        except Exception:
            open_orders = getattr(self, "live_open_orders_cached", [])
            open_sell_assets = set()
            open_buy_assets = set()

        # =========================================================================
        # 0. EXCHANGE DEAD-MAN HEARTBEAT (PROTEZIONE CADUTA SERVER)
        # =========================================================================
        if (time.time() - self.last_heartbeat_time) >= 8:
            try:
                if hasattr(self.client, 'heartbeat'):
                    self.client.heartbeat()
                self.last_heartbeat_time = time.time()
            except Exception:
                pass

        # =========================================================================
        # 1. AUTO-INVENTORY SELL GUARDIAN & COMPLETE SET MERGER
        # =========================================================================
        async with aiohttp.ClientSession() as session:
            try:
                pos_url = f"https://data-api.polymarket.com/positions?user={self.proxy_wallet}"
                async with session.get(pos_url, timeout=3) as p_resp:
                    if p_resp.status == 200:
                        positions = await p_resp.json()
                        formatted_pos = []
                        tot_pos_val = 0.0
                        for pos in positions:
                            size = float(pos.get("size", 0) or 0)
                            if size < 0.1:
                                continue
                            avg_p = float(pos.get("avgPrice", 0) or 0)
                            cur_p = float(pos.get("curPrice", 0) or 0)
                            title = pos.get("title", "")
                            asset_id = str(pos.get("asset"))
                            if title:
                                self.market_names_cache[asset_id] = title
                            val = round(size * cur_p, 2)
                            cost = round(size * avg_p, 2)
                            pnl_usd = round(val - cost, 2)
                            pnl_pct = round(((cur_p - avg_p) / avg_p) * 100.0, 1) if avg_p > 0 else 0.0
                            tot_pos_val += val
                            formatted_pos.append({
                                "token_id": asset_id,
                                "title": title or f"Posizione {asset_id[:8]}...",
                                "size": round(size, 1),
                                "avg_price": round(avg_p, 3),
                                "cur_price": round(cur_p, 3),
                                "current_val": val,
                                "pnl_usd": pnl_usd,
                                "pnl_pct": pnl_pct
                            })
                        self.cached_positions = formatted_pos
                        self.cached_positions_val = round(tot_pos_val, 2)

                # Fetch real executed trades from Polymarket Data API
                trades_url = f"https://data-api.polymarket.com/trades?user={self.proxy_wallet}&limit=20"
                async with session.get(trades_url, timeout=3) as t_resp:
                    if t_resp.status == 200:
                        raw_trades = await t_resp.json()
                        formatted_trades = []
                        for t in raw_trades:
                            ts = t.get("timestamp")
                            time_str = time.strftime('%H:%M:%S', time.localtime(ts)) if ts else "N/D"
                            side = str(t.get("side", "BUY")).upper()
                            action = "🟢 COMPRA (BUY)" if side == "BUY" else "🔴 VENDITA (SELL)"
                            title = t.get("title") or "Mercato Polymarket"
                            size = float(t.get("size", 0) or 0)
                            price = float(t.get("price", 0) or 0)
                            formatted_trades.append({
                                "time": time_str,
                                "action": action,
                                "market": title,
                                "shares": size,
                                "price": price,
                                "pnl": None
                            })
                        self.cached_trades = formatted_trades

                        # Scan and execute automated Complete Set Merges (YES+NO -> USDC)
                        try:
                            self.mergeable_pairs = self.token_merger.find_mergeable_pairs(positions)
                            if self.enable_auto_merge and self.mergeable_pairs:
                                for mp in self.mergeable_pairs:
                                    cid = mp.get("condition_id")
                                    shares = mp.get("mergeable_shares", 0)
                                    is_neg = mp.get("is_neg_risk", True)
                                    if shares >= 0.5 and cid:
                                        print(f"[{now_str}] 💎 ESECUZIONE AUTOMATICA FUSIONE ON-CHAIN: {shares} quote su '{mp['market'][:25]}' -> Incasso: {mp['expected_usdc']}$ USDC...")
                                        tx_h = self.token_merger.execute_merge(cid, shares, is_neg)
                                        if tx_h:
                                            if not hasattr(self, "merge_history"):
                                                self.merge_history = []
                                            self.merge_history.append({
                                                "time": now_str,
                                                "market": mp["market"],
                                                "shares": shares,
                                                "payout": mp["expected_usdc"],
                                                "tx_hash": tx_h
                                            })
                        except Exception as me:
                            pass
                        for pos in positions:
                            size = float(pos.get("size", 0) or 0)
                            avg_p = float(pos.get("avgPrice", 0) or 0)
                            cur_p = float(pos.get("curPrice", 0) or 0)
                            asset_id = str(pos.get("asset"))
                            title = pos.get("title", "")

                            if title:
                                self.market_names_cache[asset_id] = title

                            if size >= 1.0 and avg_p > 0 and cur_p >= 0.01 and asset_id not in open_sell_assets:
                                target_sell = round(max(avg_p * 1.18, cur_p + 0.02), 3)
                                target_sell = min(0.999, max(0.001, target_sell))

                                print(f"[{now_str}] 📌 AUTO-CREAZIONE ORDINE DI VENDITA: {size} quote di '{title[:25]}' ad ASK target {target_sell:.3f}$ (+18% margine)...")
                                try:
                                    sell_args = OrderArgs(price=target_sell, size=round(size, 1), side=SELL, token_id=asset_id)
                                    s_res = self.client.post_order(self.client.create_order(sell_args), OrderType.GTC)
                                    if s_res.get("success") or s_res.get("orderID"):
                                        print(f"[+] ✅ ORDINE DI VENDITA CONFERMATO SUL BOOK!")
                                        open_sell_assets.add(asset_id)
                                except Exception as se:
                                    print(f"[!] Errore creazione vendita: {se}")

                            if size >= 1.0 and avg_p > 0 and cur_p >= 0.01:
                                pnl_pct = ((cur_p - avg_p) / avg_p) * 100.0
                                cost = size * avg_p
                                val = size * cur_p
                                pnl_usd = val - cost

                                if pnl_pct >= self.take_profit_pct:
                                    print(f"[{now_str}] 🚀 AUTO TAKE-PROFIT: '{title[:25]}'! PnL: +{pnl_pct:.1f}% (+{pnl_usd:.2f}$)")
                                    await self.execute_market_exit(asset_id, size, cur_p, "TAKE-PROFIT", title, pnl_usd, now_str)

                                elif pnl_pct <= self.stop_loss_pct:
                                    print(f"[{now_str}] 🛑 AUTO STOP-LOSS: '{title[:25]}'! PnL: {pnl_pct:.1f}% ({pnl_usd:.2f}$)")
                                    await self.execute_market_exit(asset_id, size, cur_p, "STOP-LOSS", title, pnl_usd, now_str)
            except Exception:
                pass

        # =========================================================================
        # 2. SCREENER (DUAL-ALPHA REWARDS + MARKET MAKING + SPREAD LOGICO)
        # =========================================================================
        if (time.time() - self.last_scan_time) >= 10:
            markets, _ = await get_all_active_markets(total_to_fetch=1200)
            self.rewards_screener, self.hft_screener, self.wide_screener = filter_dual_engine_markets(markets, exclude_sports=self.exclude_sports)
            self.current_screener = (self.rewards_screener[:4] + self.hft_screener[:2] + self.wide_screener[:2])
            for m in markets:
                for clob_id in m.get("clob_token_ids", []):
                    self.market_names_cache[str(clob_id)] = m.get("question", "")
            self.last_scan_time = time.time()

        if (time.time() - self.last_logical_scan_time) >= 15:
            try:
                self.logical_opportunities = await fetch_all_logical_opportunities(exclude_sports=self.exclude_sports, total_events_to_scan=200)
                self.last_logical_scan_time = time.time()
            except Exception:
                pass

        # =========================================================================
        # 3. PIAZZAMENTO NUOVI ORDINI DI ACQUISTO (DUAL-ALPHA REWARDS + AVELLANEDA-STOIKOV)
        # =========================================================================
        avail_collateral = self.get_clob_collateral()
        busy_tokens = open_buy_assets.union(open_sell_assets)

        # Motore Avellaneda-Stoikov Dual-Bidding con Priorità Rewards
        if self.enable_as_mm and avail_collateral >= 2.0 and len(open_orders) < self.max_total_open_orders:
            candidates = (self.rewards_screener + self.hft_screener + self.wide_screener)[:8]
            for cand in candidates:
                token_id_yes = str(cand.get("token_id", ""))
                tokens_raw = cand.get("clob_token_ids", [])
                if isinstance(tokens_raw, list) and len(tokens_raw) >= 2:
                    token_id_yes = str(tokens_raw[0])
                    token_id_no = str(tokens_raw[1])
                else:
                    continue

                if token_id_yes in busy_tokens or token_id_no in busy_tokens:
                    continue

                m_id = str(cand.get("id", token_id_yes))
                yes_bid_live = float(cand.get("raw_best_bid", 0.45) or 0.45)
                yes_ask_live = float(cand.get("raw_best_ask", 0.55) or 0.55)
                r_min_size = float(cand.get("rewards_min_size", 0) or 0)
                r_max_spread = float(cand.get("rewards_max_spread", 0) or 0)
                r_daily = float(cand.get("rewards_daily", 0) or 0)

                # Calcolo Avellaneda-Stoikov
                as_res = self.as_engine.compute_quotes(
                    market_id=m_id,
                    yes_best_bid=yes_bid_live,
                    yes_best_ask=yes_ask_live,
                    no_best_bid=round(1.0 - yes_ask_live, 3) if yes_ask_live else None,
                    no_best_ask=round(1.0 - yes_bid_live, 3) if yes_bid_live else None
                )
                self.last_as_quotes[m_id] = {
                    "market": cand.get("Mercato", ""),
                    "fair_value": as_res.fair_value,
                    "r": as_res.reservation_price,
                    "delta": as_res.half_spread,
                    "vol": as_res.volatility,
                    "yes_bid": as_res.yes_bid_price,
                    "no_bid": as_res.no_bid_price,
                    "expected_edge": as_res.expected_edge,
                    "rewards_daily": r_daily,
                    "rewards_min_size": r_min_size
                }

                if as_res.yes_bid_price and as_res.no_bid_price and as_res.expected_edge >= 0.002:
                    # Se il mercato ha Rewards, adatta la size per soddisfare la soglia minima
                    if r_min_size > 0 and (r_min_size * (as_res.yes_bid_price + as_res.no_bid_price)) <= avail_collateral:
                        size_yes = r_min_size
                        size_no = r_min_size
                        badge_type = f"🎁 DUAL-ALPHA REWARDS ({r_daily:.0f}$/gg)"
                    else:
                        size_yes = max(5.0, round(as_res.yes_shares, 1))
                        size_no = max(5.0, round(as_res.no_shares, 1))
                        badge_type = "🧠 AVELLANEDA-STOIKOV"

                    cost_yes = round(size_yes * as_res.yes_bid_price, 2)
                    cost_no = round(size_no * as_res.no_bid_price, 2)

                    if avail_collateral >= (cost_yes + cost_no):
                        try:
                            print(f"[{now_str}] {badge_type} su '{cand['Mercato'][:20]}': BUY YES @ {as_res.yes_bid_price:.3f}$ ({size_yes}q) + BUY NO @ {as_res.no_bid_price:.3f}$ ({size_no}q) | Spesa: {(cost_yes+cost_no):.2f}$ | Edge: +{as_res.expected_edge:.3f}$")
                            # 1. Order YES
                            args_yes = OrderArgs(price=round(as_res.yes_bid_price, 3), size=size_yes, side=BUY, token_id=token_id_yes)
                            res_yes = self.client.post_order(self.client.create_order(args_yes), OrderType.GTC)
                            # 2. Order NO
                            args_no = OrderArgs(price=round(as_res.no_bid_price, 3), size=size_no, side=BUY, token_id=token_id_no)
                            res_no = self.client.post_order(self.client.create_order(args_no), OrderType.GTC)

                            if (res_yes.get("success") or res_yes.get("orderID")) and (res_no.get("success") or res_no.get("orderID")):
                                print(f"[+] ✅ DUAL-ALPHA PIAZZATO CON SUCCESSO SUL BOOK (Qualificato per Rewards + Spread)!")
                                busy_tokens.add(token_id_yes)
                                busy_tokens.add(token_id_no)
                                avail_collateral -= (cost_yes + cost_no)
                                break
                        except Exception as as_err:
                            print(f"[!] Errore piazzamento Dual-Alpha: {as_err}")



    async def execute_market_exit(self, asset_id, size, cur_price, exit_type, title, pnl_usd, now_str):
        try:
            open_orders = self.client.get_open_orders()
            matching_hashes = [o.get("id") or o.get("orderID") for o in open_orders if str(o.get("asset_id")) == str(asset_id)]
            if matching_hashes:
                self.client.cancel_orders(matching_hashes)

            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://clob.polymarket.com/book?token_id={asset_id}", timeout=3) as b_resp:
                    if b_resp.status == 200:
                        book_data = await b_resp.json()
                        bids = sorted(book_data.get("bids", []), key=lambda x: float(x["price"]), reverse=True)
                        best_bid = float(bids[0]["price"]) if bids else cur_price
                    else:
                        best_bid = cur_price

            best_bid = max(0.001, min(0.999, round(best_bid, 3)))
            sell_args = OrderArgs(price=best_bid, size=round(size, 2), side=SELL, token_id=asset_id)
            self.client.post_order(self.client.create_order(sell_args), OrderType.GTC)
            print(f"[+] ✅ {exit_type} ESEGUITO A {best_bid:.3f}$! PnL: {pnl_usd:+.2f}$")
            
            self.trade_history.append({
                "time": now_str,
                "strategy": "GUARDIAN",
                "action": f"{exit_type}",
                "market": title,
                "price": best_bid,
                "shares": size,
                "pnl": round(pnl_usd, 2)
            })
            self.active_real_orders = [o for o in self.active_real_orders if str(o.get("token_id")) != str(asset_id)]
        except Exception as e:
            print(f"[!] Errore {exit_type}: {e}")

    def cancel_all_orders(self):
        try:
            open_orders = self.client.get_open_orders()
            hashes = [o.get("id") or o.get("orderID") for o in open_orders if o.get("id") or o.get("orderID")]
            if hashes:
                self.client.cancel_orders(hashes)
            self.active_real_orders.clear()
            print("[+] Tutti gli ordini sono stati cancellati.")
        except Exception as e:
            print(f"[!] Errore cancellazione: {e}")

engine = CompletePolymarketQuantBot()

async def handle_index(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def handle_logical_page(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "logical_spread.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")

# =========================================================================
# CACHED HIGH-PERFORMANCE NON-BLOCKING STATUS HANDLER
# =========================================================================
_status_cache = {}
_status_cache_ts = 0.0

async def handle_status(request):
    global _status_cache, _status_cache_ts
    now = time.time()
    if _status_cache and (now - _status_cache_ts) < 1.0:
        return web.json_response(_status_cache)

    try:
        cash = float(getattr(engine, "_cached_cash", 12.55))
        positions_market_val = float(getattr(engine, "cached_positions_val", 0.0))
        formatted_positions = getattr(engine, "cached_positions", [])

        open_orders = getattr(engine, "live_open_orders_cached", [])
        if not open_orders:
            try:
                open_orders = engine.client.get_open_orders()
                engine.live_open_orders_cached = open_orders
            except Exception:
                open_orders = []

        buy_orders = []
        sell_orders = []
        for o in open_orders:
            asset_id = str(o.get("asset_id", ""))
            m_title = engine.market_names_cache.get(asset_id, f"Mercato {asset_id[:10]}...")
            price = float(o.get("price", 0) or 0)
            shares = float(o.get("original_size", 0) or 0)
            side = o.get("side", "BUY")
            outcome = str(o.get("outcome", "YES")).upper()
            order_data = {
                "order_id": o.get("id") or o.get("orderID", ""),
                "market": m_title,
                "side": side,
                "token_type": outcome,
                "price": price,
                "shares": shares,
                "total_usd": round(shares * price, 2),
                "token_id": asset_id,
                "status": "PLACED"
            }
            if side == "BUY":
                buy_orders.append(order_data)
            else:
                sell_orders.append(order_data)

        in_orders_val = sum(o.get("total_usd", 0.0) for o in buy_orders)

        net_worth = round(cash + positions_market_val, 2)
        if engine.initial_usdc is None:
            engine.initial_usdc = net_worth

        pnl_val = round(net_worth - engine.initial_usdc, 2)
        pnl_pct = round((pnl_val / engine.initial_usdc) * 100.0, 2) if engine.initial_usdc > 0 else 0.0

        data = {
            "net_worth": net_worth,
            "free_cash": round(cash, 2),
            "in_orders": round(in_orders_val, 2),
            "positions_val": round(positions_market_val, 2),
            "pol_gas": round(engine.pol_gas, 4),
            "pnl_val": pnl_val,
            "pnl_pct": pnl_pct,
            "total_trades": len(getattr(engine, "cached_trades", [])) + len(engine.trade_history),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "active_orders": buy_orders + sell_orders,
            "positions": formatted_positions,
            "screener": getattr(engine, "rewards_screener", [])[:4] + getattr(engine, "hft_screener", [])[:2] + getattr(engine, "wide_screener", [])[:2],
            "rewards_screener": getattr(engine, "rewards_screener", []),
            "trades": getattr(engine, "cached_trades", []) + engine.trade_history[-10:],
            "running": engine.running,
            "killswitch": engine.killswitch_triggered,
            "is_real_money": True,
            "wallet_address": engine.wallet_address,
            "enable_hft": engine.enable_hft,
            "enable_wide": engine.enable_wide,
            "enable_as_mm": engine.enable_as_mm,
            "enable_auto_merge": engine.enable_auto_merge,
            "as_gamma": engine.as_gamma,
            "last_as_quotes": engine.last_as_quotes,
            "mergeable_pairs": getattr(engine, "mergeable_pairs", []),
            "merge_history": getattr(engine, "merge_history", []),
            "exclude_sports": engine.exclude_sports,
            "max_total_open_orders": engine.max_total_open_orders,
            "max_order_spend": engine.max_order_spend,
            "take_profit_pct": engine.take_profit_pct,
            "stop_loss_pct": engine.stop_loss_pct
        }
        _status_cache = data
        _status_cache_ts = now
        return web.json_response(data)
    except Exception as e:
        return web.json_response({
            "net_worth": 1.83,
            "free_cash": 1.83,
            "in_orders": 0.0,
            "positions_val": 0.0,
            "running": engine.running,
            "buy_orders": [],
            "sell_orders": [],
            "screener": []
        })

async def handle_logical_spreads(request):
    if not engine.logical_opportunities:
        try:
            engine.logical_opportunities = await fetch_all_logical_opportunities(exclude_sports=engine.exclude_sports, total_events_to_scan=200)
        except Exception:
            pass
    return web.json_response({"opportunities": engine.logical_opportunities})

async def handle_execute_logical_basket(request):
    try:
        payload = await request.json()
        outcomes = payload.get("outcomes", [])
        sets = int(payload.get("sets", 1))

        if not outcomes or sets < 1:
            return web.json_response({"error": "Parametri paniere non validi"}, status=400)

        results = []
        for leg in outcomes:
            token_id = leg.get("token_id")
            price = float(leg.get("best_ask", 0))
            if token_id and price > 0:
                args = OrderArgs(price=price, size=sets, side=BUY, token_id=token_id)
                res = engine.client.post_order(engine.client.create_order(args), OrderType.GTC)
                results.append({"token_id": token_id, "res": res})

        return web.json_response({"success": True, "orders": results})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_update_settings(request):
    try:
        payload = await request.json()
        if "enable_hft" in payload:
            engine.enable_hft = bool(payload["enable_hft"])
        if "enable_wide" in payload:
            engine.enable_wide = bool(payload["enable_wide"])
        if "exclude_sports" in payload:
            engine.exclude_sports = bool(payload["exclude_sports"])
        if "max_total_open_orders" in payload:
            engine.max_total_open_orders = max(1, min(6, int(payload["max_total_open_orders"])))
        if "max_order_spend" in payload:
            engine.max_order_spend = max(0.50, min(5.00, float(payload["max_order_spend"])))
        if "take_profit_pct" in payload:
            engine.take_profit_pct = max(5.0, min(100.0, float(payload["take_profit_pct"])))
        if "stop_loss_pct" in payload:
            engine.stop_loss_pct = -abs(float(payload["stop_loss_pct"]))

        print(f"[⚙️ SETTINGS AGGIORNATI] HFT: {engine.enable_hft} | WIDE: {engine.enable_wide} | Escludi Sport: {engine.exclude_sports}")
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_orderbook(request):
    token_id = request.query.get("token_id")
    if not token_id:
        if engine.active_real_orders:
            token_id = engine.active_real_orders[0].get("token_id")
        elif engine.current_screener:
            token_id = engine.current_screener[0].get("token_id")
        else:
            token_id = "5615282760875985231868508008056959876238536896643315063916840237042205273721"

    async with aiohttp.ClientSession() as session:
        try:
            book_url = f"https://clob.polymarket.com/book?token_id={token_id}"
            trades_url = f"https://data-api.polymarket.com/trades?asset_id={token_id}&limit=6"
            meta_url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
            
            book_task = session.get(book_url, timeout=3)
            trades_task = session.get(trades_url, timeout=3)
            meta_task = session.get(meta_url, timeout=3)
            book_resp, trades_resp, meta_resp = await asyncio.gather(book_task, trades_task, meta_task, return_exceptions=True)

            bids, asks = [], []
            if not isinstance(book_resp, Exception) and book_resp.status == 200:
                data = await book_resp.json()
                bids = sorted(data.get("bids", []), key=lambda x: float(x.get("price")), reverse=True)[:8]
                asks = sorted(data.get("asks", []), key=lambda x: float(x.get("price")))[:8]

            polymarket_url = "https://polymarket.com"
            market_full_title = ""
            if not isinstance(meta_resp, Exception) and meta_resp.status == 200:
                meta_data = await meta_resp.json()
                if isinstance(meta_data, list) and len(meta_data) > 0:
                    first_m = meta_data[0]
                    market_full_title = first_m.get("question", "")
                    events = first_m.get("events", [])
                    event_slug = events[0].get("slug") if events else ""
                    market_slug = first_m.get("slug", "")
                    if event_slug:
                        polymarket_url = f"https://polymarket.com/event/{event_slug}"
                    elif market_slug:
                        polymarket_url = f"https://polymarket.com/market/{market_slug}"

            recent_trades = []
            last_trade_text = "Nessun trade recente"
            if not isinstance(trades_resp, Exception) and trades_resp.status == 200:
                raw_trades = await trades_resp.json()
                if isinstance(raw_trades, list) and len(raw_trades) > 0:
                    for t in raw_trades[:5]:
                        ts = t.get("timestamp")
                        p = float(t.get("price", 0) or 0)
                        s = float(t.get("size", 0) or 0)
                        side = t.get("side", "BUY")
                        time_ago = format_timestamp_ago(ts)
                        recent_trades.append({
                            "price": f"{p:.3f} $",
                            "size": f"{s:,.1f} q",
                            "side": side,
                            "time_ago": time_ago
                        })
                    first_t = raw_trades[0]
                    last_trade_text = f"{float(first_t.get('price', 0)):.3f} $ ({format_timestamp_ago(first_t.get('timestamp'))})"

            profit_estimate = {
                "has_active_order": False,
                "shares": 0,
                "buy_price": 0.0,
                "sell_price": 0.0,
                "cost": 0.0,
                "revenue": 0.0,
                "profit_usd": 0.0,
                "profit_pct": 0.0,
                "desc": ""
            }

            active_order = next((o for o in engine.active_real_orders if o.get("token_id") == token_id), None)
            if active_order:
                shares = active_order["shares"]
                buy_p = active_order["buy_price"]
                sell_p = active_order["sell_price"]
                cost = shares * buy_p
                revenue = shares * sell_p
                profit_usd = revenue - cost
                profit_pct = (profit_usd / cost) * 100.0 if cost > 0 else 0.0
                strat_name = "⚡ HFT Rapido" if active_order.get("strategy") == "HFT" else "💎 Spread Largo"

                profit_estimate = {
                    "has_active_order": True,
                    "shares": shares,
                    "buy_price": buy_p,
                    "sell_price": sell_p,
                    "cost": round(cost, 2),
                    "revenue": round(revenue, 2),
                    "profit_usd": round(profit_usd, 2),
                    "profit_pct": round(profit_pct, 1),
                    "desc": f"[{strat_name}] BID {buy_p:.3f}$ -> ASK {sell_p:.3f}$ (Spesa: {cost:.2f}$ -> Incasso: {revenue:.2f}$ su {shares} quote)"
                }
            elif bids and asks:
                top_bid = float(bids[0]["price"])
                top_ask = float(asks[0]["price"])
                my_bid = round(top_bid + 0.001, 3)
                my_ask = round(top_ask - 0.001, 3)
                shares = max(5, int(engine.max_order_spend / my_bid))
                cost = shares * my_bid
                revenue = shares * my_ask
                profit_usd = revenue - cost
                profit_pct = (profit_usd / cost) * 100.0 if cost > 0 else 0.0

                profit_estimate = {
                    "has_active_order": False,
                    "shares": shares,
                    "buy_price": my_bid,
                    "sell_price": my_ask,
                    "cost": round(cost, 2),
                    "revenue": round(revenue, 2),
                    "profit_usd": round(profit_usd, 2),
                    "profit_pct": round(profit_pct, 1),
                    "desc": f"Stima Spread Market Making: BID {my_bid:.3f}$ -> ASK {my_ask:.3f}$ ({shares} quote)"
                }

            my_exact_prices = []
            try:
                open_orders = engine.client.get_open_orders()
                for o in open_orders:
                    if str(o.get("asset_id")) == str(token_id):
                        my_exact_prices.append(round(float(o.get("price")), 3))
            except Exception:
                my_exact_prices = [round(o["buy_price"], 3) for o in engine.active_real_orders if o.get("token_id") == token_id]

            return web.json_response({
                "token_id": token_id,
                "market_full_title": market_full_title,
                "polymarket_url": polymarket_url,
                "bids": bids,
                "asks": asks,
                "my_prices": my_exact_prices,
                "last_trade_text": last_trade_text,
                "recent_trades": recent_trades,
                "profit_estimate": profit_estimate
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"bids": [], "asks": []})

async def handle_toggle(request):
    engine.running = not engine.running
    return web.json_response({"running": engine.running})

async def handle_emergency_stop(request):
    engine.running = False
    engine.cancel_all_orders()
    return web.json_response({"success": True, "message": "EMERGENCY STOP ESEGUITO"})

async def handle_training_page(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "training.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def handle_training_status(request):
    return web.json_response(trainer.get_status())

async def handle_training_dataset(request):
    return web.json_response({"samples": trainer.dataset_sample[:30]})

async def handle_training_start(request):
    if not trainer.is_training:
        asyncio.create_task(trainer.run_training_loop())
    return web.json_response({"success": True, "message": "Addestramento avviato"})

async def handle_training_apply(request):
    # Applica i migliori parametri trovati dall'AI direttamente al motore live
    bp = trainer.best_params
    engine.as_gamma = bp.get("gamma", 0.15)
    engine.as_engine.gamma = engine.as_gamma
    engine.as_engine.delta_min_ticks = bp.get("delta_min_ticks", 2)
    engine.as_engine.c_vol = bp.get("c_vol", 1.5)
    engine.as_engine.q_max_usdc = bp.get("q_max_usdc", 12.0)
    print(f"[🤖 AI TUNING APPLICATO] Gamma: {engine.as_gamma} | Spread Min: {engine.as_engine.delta_min_ticks}t | Cap: {engine.as_engine.q_max_usdc}$")
    return web.json_response({"success": True, "applied": bp})

async def handle_simulation_page(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "simulation.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def handle_simulation_status(request):
    return web.json_response(sim_engine.get_status())

async def handle_simulation_toggle(request):
    sim_engine.is_running = not sim_engine.is_running
    return web.json_response({"is_running": sim_engine.is_running})

async def handle_simulation_reset(request):
    sim_engine.reset()
    return web.json_response({"success": True, "balance": sim_engine.virtual_balance})

async def handle_reset_pnl(request):
    engine.initial_usdc = None
    return web.json_response({"success": True, "message": "PnL azzerato al valore corrente"})

def create_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/logical", handle_logical_page)
    app.router.add_get("/training", handle_training_page)
    app.router.add_get("/simulation", handle_simulation_page)
    app.router.add_get("/api/simulation/status", handle_simulation_status)
    app.router.add_post("/api/simulation/toggle", handle_simulation_toggle)
    app.router.add_post("/api/simulation/reset", handle_simulation_reset)
    app.router.add_get("/api/training/status", handle_training_status)
    app.router.add_get("/api/training/dataset", handle_training_dataset)
    app.router.add_post("/api/training/start", handle_training_start)
    app.router.add_post("/api/training/apply", handle_training_apply)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/reset_pnl", handle_reset_pnl)
    app.router.add_get("/api/book", handle_orderbook)
    app.router.add_get("/api/logical_spreads", handle_logical_spreads)
    app.router.add_post("/api/execute_logical_basket", handle_execute_logical_basket)
    app.router.add_post("/api/settings", handle_update_settings)
    app.router.add_post("/api/toggle", handle_toggle)
    app.router.add_post("/api/emergency_stop", handle_emergency_stop)
    return app

async def start_background_tasks(app):
    app['trading_task'] = asyncio.create_task(engine.trading_loop())
    app['simulation_task'] = asyncio.create_task(sim_engine.simulation_loop())

async def cleanup_background_tasks(app):
    app['trading_task'].cancel()
    app['simulation_task'].cancel()
    await asyncio.gather(app['trading_task'], app['simulation_task'], return_exceptions=True)

if __name__ == "__main__":
    app = create_app()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    web.run_app(app, host="127.0.0.1", port=8080)
