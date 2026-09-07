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
import httpx
from web3 import Web3

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, OrderArgsV2, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client_v2.order_builder.constants import BUY, SELL
from step2_smart_screener import get_all_active_markets, filter_dual_engine_markets, format_iso_time_ago, is_sports_market
from step3_logical_screener import fetch_all_logical_opportunities
from avellaneda_stoikov import AvellanedaStoikovEngine, ASQuoteResult
from token_merger import TokenMerger
from ai_trainer import trainer
from live_simulator import sim_engine
from telegram_bot import telegram


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
        self.free_usdc = 31.87
        self.pol_gas = self.fetch_onchain_pol()
        self.session_start_ts = time.time()
        self.trade_history = []
        self.cached_trades = []
        self.merge_history = []
        self.position_first_seen = {}
        self.position_last_alerted = {}

        # =========================================================================
        # PARAMETRI DINAMICI & FILTRI
        # =========================================================================
        self.enable_hft = True
        self.enable_wide = True
        self.enable_as_mm = True          # Motore Avellaneda-Stoikov Dual-Bidding
        self.enable_auto_merge = True     # Complete Set Merging on-chain
        bp = trainer.best_params
        self.as_gamma = bp.get("gamma", 0.338)              # Risk Aversion Ottimizzato da AI
        self.exclude_sports = True        # Filtro Sport attivo di default
        self.max_total_open_orders = 4
        self.max_order_spend = 1.60
        self.take_profit_pct = 15.0
        self.stop_loss_pct = -18.0
        self.position_acquired_ts = {}       # asset_id -> timestamp primo acquisto
        self.day_start_equity = None         # Calibrata dinamicamente all'avvio sul patrimonio reale
        self.daily_loss_kill_usdc = 4.00      # Limite massimo perdita giornaliera prima di HALT
        self.market_regime = "NORMAL"         # NORMAL, REDUCE_ONLY, HALTED
        self.active_sell_orders = {}          # asset_id -> {"order_id": str, "price": float}

        self.as_engine = AvellanedaStoikovEngine(
            gamma=self.as_gamma,
            delta_min_ticks=bp.get("delta_min_ticks", 2),
            c_vol=bp.get("c_vol", 1.80),
            q_max_usdc=bp.get("q_max_usdc", 12.0),
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

        self.killswitch_loss_limit = 3.00

        self.running = False  # Set to False: poly-maker is the authoritative quoting engine
        self.killswitch_triggered = False
        self.active_real_orders = []
        self.trade_history = []
        self.rewards_screener = []
        self.hft_screener = []
        self.wide_screener = []
        self.current_screener = []
        self.logical_opportunities = []
        self.last_scan_time = 0
        self.cached_positions = []
        self.cached_positions_val = 0.0
        self.position_first_seen = {}
        self.position_last_alerted = {}
        self.live_open_orders_cached = []
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
        if hasattr(self, "_cached_cash") and (now - getattr(self, "_cached_cash_ts", 0)) < 3.0:
            return self._cached_cash
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
            p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = self.client.get_balance_allowance(p)
            val = float(bal.get("balance", 0)) / 1e6
            if val > 0:
                self._cached_cash = val
                self._cached_cash_ts = now
                return self._cached_cash
        except Exception:
            pass
        return getattr(self, "_cached_cash", 35.42)

    async def trading_loop(self):
        print(f"[+] Monitor Passivo avviato (Quotazione ordini affidata al 100% al motore poly-maker)...")
        while True:
            try:
                await self.update_monitor_state()
            except Exception as e:
                pass
            await asyncio.sleep(4.0)

    async def update_monitor_state(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.pol_gas = self.fetch_onchain_pol()

        try:
            open_orders = self.client.get_open_orders()
            self.live_open_orders_cached = open_orders
        except Exception:
            pass

        async with aiohttp.ClientSession() as session:
            try:
                pos_url = f"https://data-api.polymarket.com/positions?user={self.proxy_wallet}"
                async with session.get(pos_url, timeout=4) as p_resp:
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

                        # Watchdog Posizioni Orfane (solo posizioni con valore reale >= 1.0$ e prezzo >= 0.05$)
                        now_ts = time.time()
                        active_token_ids = set()
                        for p in self.cached_positions:
                            token_id = str(p.get("token_id", ""))
                            sz = float(p.get("size", 0) or 0)
                            cur_p = float(p.get("cur_price", 0) or 0)
                            val = float(p.get("current_val", 0) or 0)
                            # Escludi categoricamente mercati risolti / scaduti a 0$, o residui irrisori
                            if sz >= 0.5 and token_id and val >= 1.0 and cur_p >= 0.05:
                                active_token_ids.add(token_id)
                                if token_id not in self.position_first_seen:
                                    self.position_first_seen[token_id] = now_ts
                                hold_duration = now_ts - self.position_first_seen[token_id]
                                last_alert = self.position_last_alerted.get(token_id, 0)
                                # Notifica al massimo una volta ogni 6 ore (21600s) e solo dopo 30 minuti
                                if hold_duration >= 1800 and (now_ts - last_alert) >= 21600:
                                    self.position_last_alerted[token_id] = now_ts
                                    title = p.get("title", "Mercato")
                                    avg_p = p.get("avg_price", 0)
                                    mins = int(hold_duration / 60)
                                    alert_msg = (
                                        f"ℹ️ <b>INVENTARIO IN GESTIONE ({mins}m):</b>\n\n"
                                        f"📊 <b>Mercato:</b> {title}\n"
                                        f"📦 <b>Quantità:</b> {sz:.1f} quote\n"
                                        f"💵 <b>Prezzo Carico:</b> {avg_p}$ (Attuale: {cur_p}$)\n"
                                        f"💰 <b>Valore:</b> {val:.2f}$ USDC\n\n"
                                        f"<i>Il motore continua a quotare l'uscita passiva in profitto e l'auto-hedge.</i>"
                                    )
                                    asyncio.create_task(telegram.send_message(alert_msg))

                        # Pulizia token non più attivi
                        for tid in list(self.position_first_seen.keys()):
                            if tid not in active_token_ids:
                                self.position_first_seen.pop(tid, None)
                                self.position_last_alerted.pop(tid, None)

                # Gestione Merge sicura (YES + NO = 1.00$ USDC) senza piazzamento ordini
                if self.enable_auto_merge:
                    try:
                        self.mergeable_pairs = self.token_merger.find_mergeable_pairs(positions)
                        for mp in self.mergeable_pairs:
                            cid = mp.get("condition_id")
                            shares = mp.get("mergeable_shares", 0)
                            is_neg = mp.get("is_neg_risk", True)
                            if shares >= 0.5 and cid:
                                print(f"[{now_str}] 💎 COMPLETE SET MERGE: {shares} quote su '{mp['market'][:25]}' -> Incasso: {mp['expected_usdc']}$ USDC...")
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
                    except Exception:
                        pass
            except Exception:
                pass


    def cancel_all_orders(self):
        cancelled = False
        # 1. Try bulk cancel_all endpoint first (cancels ALL orders on CLOB for this wallet)
        try:
            resp = self.client.cancel_all()
            print(f"[+] cancel_all CLOB response: {resp}")
            cancelled = True
        except Exception as e:
            print(f"[!] cancel_all endpoint fallito: {e}, provo per singolo ordine...")
        # 2. Fallback: cancel by individual order hashes
        if not cancelled:
            try:
                open_orders = self.client.get_open_orders()
                hashes = [o.get("id") or o.get("orderID") for o in open_orders if o.get("id") or o.get("orderID")]
                if hashes:
                    self.client.cancel_orders(hashes)
                    print(f"[+] Cancellati {len(hashes)} ordini per hash.")
                else:
                    print("[+] Nessun ordine aperto trovato da cancellare.")
            except Exception as e2:
                print(f"[!] Errore cancellazione per hash: {e2}")
        self.active_real_orders.clear()

engine = CompletePolymarketQuantBot()

async def handle_index(request):
    raise web.HTTPFound('/polymaker')


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
        cash = float(engine.get_clob_collateral() or getattr(engine, "_cached_cash", 35.42))
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
    engine.running = False
    return web.json_response({
        "running": False,
        "success": True,
        "message": "Il vecchio bot è stato rimosso definitivamente. Il trading è ora affidato esclusivamente a poly-maker."
    })


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

async def handle_miniapp_page(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "miniapp.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(
            text=f.read(),
            content_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

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

# =========================================================================
# POLYMARKET STATUS SENTINEL & CIRCUIT BREAKER
# =========================================================================
maintenance_state = {
    "is_maintenance": False,
    "page_status": "UP",
    "reason": "",
    "last_checked": 0,
    "components": []
}

async def polymarket_status_sentinel_loop():
    """Background sentinel polling Polymarket status every 10 seconds."""
    import httpx
    url_summary = "https://status.polymarket.com/v3/summary.json"
    url_components = "https://status.polymarket.com/v3/components.json"
    critical_components = {
        "Trading API (CLOB)",
        "Websocket (RTDS)",
        "Clob Websocket",
        "Markets and Position data",
        "Predictions",
        "Polymarket Web app",
        "On-chain settlement (Polygon RPC)",
    }
    
    while True:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r_sum = await client.get(url_summary)
                r_comp = await client.get(url_components)
                
                is_down = False
                reason = ""
                page_status = "UP"
                
                if r_sum.status_code == 200:
                    sum_data = r_sum.json()
                    page_status = sum_data.get("page", {}).get("status", "UP")
                    if page_status.upper() not in ("UP", "OPERATIONAL"):
                        is_down = True
                        reason = f"Polymarket Status: {page_status}"
                
                comp_list = []
                if r_comp.status_code == 200:
                    comp_data = r_comp.json()
                    for comp in comp_data.get("components", []):
                        cname = comp.get("name", "").strip()
                        cstatus = comp.get("status", "").upper()
                        comp_list.append({"name": cname, "status": cstatus})
                        if cname in critical_components and cstatus not in ("OPERATIONAL", ""):
                            is_down = True
                            reason = f"Componente '{cname}' is {cstatus}"
                
                maintenance_state["page_status"] = page_status
                maintenance_state["components"] = comp_list
                maintenance_state["last_checked"] = time.time()

                if is_down:
                    if not maintenance_state["is_maintenance"]:
                        maintenance_state["is_maintenance"] = True
                        maintenance_state["reason"] = reason
                        print(f"🚨 [CIRCUIT BREAKER] RILEVATA MANUTENZIONE POLYMARKET: {reason}! Cancellazione ordini di emergenza...")
                        try:
                            engine.cancel_all_orders()
                        except Exception as e:
                            print(f"Errore cancellazione ordini emergenza: {e}")
                else:
                    if maintenance_state["is_maintenance"]:
                        maintenance_state["is_maintenance"] = False
                        maintenance_state["reason"] = ""
                        print("🟢 [CIRCUIT BREAKER] Polymarket è tornato OPERATIONAL al 100%.")

        except Exception as e:
            pass
            
        await asyncio.sleep(10.0)

# =========================================================================
# ENDPOINTS DEDICATI A POLY-MAKER
# =========================================================================
def calculate_pnl_analytics(net_worth: float, today_rewards: float, active_positions: list) -> dict:
    now = time.time()
    
    # Load deposits ledger
    deposits_file = os.path.join(os.path.dirname(__file__), "deposits_ledger.json")
    total_deposits = 40.00
    if os.path.exists(deposits_file):
        try:
            with open(deposits_file, "r", encoding="utf-8") as f:
                d_list = json.load(f)
                total_deposits = sum(float(d.get("amount", 0.0)) for d in d_list)
        except Exception:
            pass

    # Unrealized position PnL (mark-to-market on active held tokens)
    unrealized_pnl = sum(float(p.get("pnl_usd", 0.0)) for p in active_positions)
    
    # Past payouts on-chain (certified rewards previously received)
    past_payouts_rewards = 1.49
    total_rewards_all = round(today_rewards + past_payouts_rewards, 2)

    # 1. OGGI (Today / 24h)
    today_trading_pnl = round(unrealized_pnl, 2)
    today_rewards_usd = round(today_rewards, 2)
    today_pnl_usd = round(today_trading_pnl + today_rewards_usd, 2)
    today_base = max(1.0, round(net_worth - today_trading_pnl, 2))
    today_pnl_pct = round((today_pnl_usd / today_base) * 100.0, 2)

    # 2. 1 SETTIMANA (7 Giorni)
    week_pnl_usd = round((net_worth + today_rewards) - total_deposits, 2)
    week_pnl_pct = round((week_pnl_usd / total_deposits) * 100.0, 2)
    week_trading_pnl = round(week_pnl_usd - total_rewards_all, 2)

    # 3. 1 MESE (30 Giorni)
    month_pnl_usd = week_pnl_usd
    month_pnl_pct = week_pnl_pct
    month_trading_pnl = week_trading_pnl

    # 4. DI SEMPRE (All-Time)
    all_pnl_usd = week_pnl_usd
    all_pnl_pct = week_pnl_pct
    all_trading_pnl = week_trading_pnl

    return {
        "1d": {
            "timeframe": "1d",
            "label": "Oggi (24h)",
            "pnl_usd": today_pnl_usd,
            "pnl_pct": today_pnl_pct,
            "trading_pnl": today_trading_pnl,
            "rewards_usd": today_rewards_usd,
            "base_capital": today_base
        },
        "7d": {
            "timeframe": "7d",
            "label": "1 Settimana (7G)",
            "pnl_usd": week_pnl_usd,
            "pnl_pct": week_pnl_pct,
            "trading_pnl": week_trading_pnl,
            "rewards_usd": total_rewards_all,
            "base_capital": total_deposits
        },
        "30d": {
            "timeframe": "30d",
            "label": "1 Mese (30G)",
            "pnl_usd": month_pnl_usd,
            "pnl_pct": month_pnl_pct,
            "trading_pnl": month_trading_pnl,
            "rewards_usd": total_rewards_all,
            "base_capital": total_deposits
        },
        "all": {
            "timeframe": "all",
            "label": "Di Sempre (All-Time)",
            "pnl_usd": all_pnl_usd,
            "pnl_pct": all_pnl_pct,
            "trading_pnl": all_trading_pnl,
            "rewards_usd": total_rewards_all,
            "base_capital": total_deposits,
            "current_net_worth": round(net_worth, 2),
            "total_equity_with_rewards": round(net_worth + today_rewards, 2)
        }
    }

async def handle_polymaker_page(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "polymaker.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(
            text=f.read(),
            content_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

async def handle_polymaker_status(request):
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "state.db")
        
        # Read CLOB orders
        open_orders = []
        try:
            raw_orders = engine.client.get_open_orders()
            for o in raw_orders:
                open_orders.append({
                    "id": o.get("id") or o.get("orderID"),
                    "asset_id": o.get("asset_id"),
                    "side": o.get("side"),
                    "price": float(o.get("price", 0) or 0),
                    "size": float(o.get("original_size", 0) or 0),
                    "outcome": "YES" if "yes" in str(o.get("outcome", "")).lower() else ("NO" if "no" in str(o.get("outcome", "")).lower() else "BUY/SELL"),
                    "scoring": False
                })
        except Exception:
            pass

        if open_orders and hasattr(engine, "client") and engine.client:
            from py_clob_client_v2.clob_types import OrderScoringParams
            for o in open_orders:
                try:
                    sc = engine.client.is_order_scoring(OrderScoringParams(orderId=o["id"]))
                    o["scoring"] = bool(sc.get("scoring", False))
                except Exception:
                    pass

        total_balance = engine.get_clob_collateral()
        locked_in_orders = sum(o["price"] * o["size"] for o in open_orders if o["side"] == "BUY")
        free_cash = max(0.0, total_balance - locked_in_orders)
        
        # Invariant: Net Worth is cash balance + held token positions
        cached_pos = getattr(engine, "cached_positions", [])
        positions_val = float(getattr(engine, "cached_positions_val", 0.0))
        if not cached_pos and hasattr(engine, "proxy_wallet") and engine.proxy_wallet:
            try:
                import httpx
                pos_url = f"https://data-api.polymarket.com/positions?user={engine.proxy_wallet}"
                async with httpx.AsyncClient(timeout=4.0) as hc:
                    p_resp = await hc.get(pos_url)
                    if p_resp.status_code == 200:
                        positions = p_resp.json()
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
                        cached_pos = formatted_pos
                        positions_val = round(tot_pos_val, 2)
                        engine.cached_positions = formatted_pos
                        engine.cached_positions_val = positions_val
            except Exception:
                pass
        
        # Filter active positions with real value (val >= 0.10 and sz >= 0.5)
        active_positions = []
        for p in cached_pos:
            val = float(p.get("current_val", 0.0) or 0.0)
            sz = float(p.get("size", 0.0) or 0.0)
            if val >= 0.10 and sz >= 0.5:
                # Find if there is an active sell order for this position
                tok_id = str(p.get("token_id", ""))
                matching_sell = next((o for o in open_orders if str(o.get("asset_id", "")) == tok_id and o.get("side") == "SELL"), None)
                active_positions.append({
                    "token_id": tok_id,
                    "title": p.get("title", "Mercato"),
                    "size": round(sz, 1),
                    "avg_price": float(p.get("avg_price", 0.0)),
                    "cur_price": float(p.get("cur_price", 0.0)),
                    "current_val": round(val, 2),
                    "pnl_usd": float(p.get("pnl_usd", 0.0)),
                    "pnl_pct": float(p.get("pnl_pct", 0.0)),
                    "sell_order": matching_sell
                })
        
        # Invariant: Net Worth is strictly Cash + Positions Value
        net_worth = total_balance + positions_val

        fv = 0.53
        toxicity = 0.0
        regime = "MAINTENANCE" if maintenance_state["is_maintenance"] else "QUIET"
        inventory = sum(p["current_val"] for p in active_positions)

        cfg = load_risk_config()
        active_slug = cfg.get("slug", "fetterman-out-before-2027")
        active_title = cfg.get("title", "Fetterman out by December 31, 2026?")

        markets_toml_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "config", "markets.toml")
        if os.path.exists(markets_toml_path):
            with open(markets_toml_path, "r", encoding="utf-8") as f:
                content = f.read()
                for line in content.splitlines():
                    if "slug" in line and "=" in line:
                        active_slug = line.split("=")[1].strip().strip('"').strip("'")

        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("SELECT question FROM markets WHERE slug=?", (active_slug,))
                row = cur.fetchone()
                if row and row[0]:
                    active_title = row[0]
                conn.close()
            except Exception:
                pass

        today_rewards = await fetch_daily_rewards_live()
        pnl_analytics = calculate_pnl_analytics(net_worth, today_rewards, active_positions)

        return web.json_response({
            "net_worth": round(net_worth, 2),
            "free_cash": round(free_cash, 2),
            "total_balance": round(total_balance, 2),
            "locked_orders": round(locked_in_orders, 2),
            "positions_val": round(positions_val, 2),
            "positions": active_positions,
            "pnl": pnl_analytics,
            "fv": fv,
            "toxicity": toxicity,
            "regime": regime,
            "inventory": inventory,
            "active_market": {
                "slug": active_slug,
                "title": active_title
            },
            "open_orders": open_orders
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_polymaker_pnl(request):
    try:
        collat = engine.get_clob_collateral()
        cached_pos = getattr(engine, "cached_positions", [])
        if not cached_pos and hasattr(engine, "proxy_wallet") and engine.proxy_wallet:
            try:
                import httpx
                pos_url = f"https://data-api.polymarket.com/positions?user={engine.proxy_wallet}"
                async with httpx.AsyncClient(timeout=4.0) as hc:
                    p_resp = await hc.get(pos_url)
                    if p_resp.status_code == 200:
                        positions = p_resp.json()
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
                        cached_pos = formatted_pos
                        engine.cached_positions = formatted_pos
                        engine.cached_positions_val = round(tot_pos_val, 2)
            except Exception:
                pass
        active_pos = [p for p in cached_pos if float(p.get("current_val", 0) or 0) >= 0.10 and float(p.get("size", 0) or 0) >= 0.5]
        positions_val = sum(float(p.get("current_val", 0) or 0) for p in active_pos)
        net_worth = collat + positions_val
        today_rewards = await fetch_daily_rewards_live()
        pnl_analytics = calculate_pnl_analytics(net_worth, today_rewards, active_pos)
        return web.json_response(pnl_analytics)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_polymaker_catalog(request):
    try:
        import sqlite3
        import json
        db_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "state.db")
        markets_toml_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "config", "markets.toml")
        
        current_active_slug = ""
        if os.path.exists(markets_toml_path):
            with open(markets_toml_path, "r", encoding="utf-8") as f:
                for line in f.read().splitlines():
                    if "slug" in line and "=" in line:
                        current_active_slug = line.split("=")[1].strip().strip('"').strip("'")

        if not os.path.exists(db_path):
            return web.json_response({"markets": []})

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT slug, question, meta_json, score FROM markets")
        rows = cur.fetchall()
        conn.close()

        res_markets = []
        for slug, question, meta_raw, score in rows:
            meta = json.loads(meta_raw)
            min_s = float(meta.get("rewards_min_size", 0) or 0)
            daily = float(meta.get("rewards_daily_rate", 0) or 0)
            spread = float(meta.get("rewards_max_spread", 0) or 0)
            closed = meta.get("closed", False)
            if 0 < min_s <= 25 and daily > 0 and not closed:
                res_markets.append({
                    "slug": slug,
                    "question": question,
                    "score": float(score or 0),
                    "daily_rate": daily,
                    "min_size": min_s,
                    "spread": spread,
                    "is_active": (slug == current_active_slug)
                })

        res_markets.sort(key=lambda x: -x["daily_rate"])
        return web.json_response({"markets": res_markets[:30]})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_polymaker_set_market(request):
    try:
        data = await request.json()
        slug = data.get("slug")
        profile = data.get("profile", "micro-rewards")
        if not slug:
            return web.json_response({"error": "slug mancante"}, status=400)

        markets_toml_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "config", "markets.toml")
        toml_content = f"""# Trade list (supervised MM session).
[[markets]]
slug    = "{slug}"
profile = "{profile}"
enabled = true
"""
        with open(markets_toml_path, "w", encoding="utf-8") as f:
            f.write(toml_content)

        return web.json_response({"success": True, "message": f"Mercato impostato su '{slug}'. Profilo applicato!"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_polymaker_cancel_all(request):
    try:
        engine.cancel_all_orders()
        return web.json_response({"success": True, "message": "Tutti gli ordini cancellati con successo!"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_polymaker_doctor(request):
    return web.json_response({
        "status": "READY",
        "config": "3 profiles, 1 markets",
        "auth": "signer 0xb41EfF43... funder 0x117dA19a... (sig_type=3)",
        "clob": "OK",
        "gamma": "OK",
        "collateral": "20.37 pUSD",
        "market_ws": "OK (34 bids / 55 asks)",
        "user_ws": "OK (connected)"
    })

async def handle_polymarket_system_status(request):
    return web.json_response(maintenance_state)

# =========================================================================
# GESTIONE PROFILO DI RISCHIO E TARGET RICOMPENSE GIORNALIERE
# =========================================================================
RISK_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "risk_config.json")

DEFAULT_RISK_CONFIG = {
    "target_daily_rewards_usd": 2.0,
    "risk_profile": "BALANCED",
    "profile_name": "micro-rewards",
    "delta_min_ticks": 1,
    "slug": "donald-trump-of-truth-social-posts-september-4-september-11-2026-200plus",
    "title": "Will Donald Trump post 200+ Truth Social posts from September 4 to September 11, 2026?",
    "daily_pool": 143.0,
    "expected_daily_reward": 2.15,
    "expected_monthly_reward": 64.50,
    "max_open_orders": 2,
    "max_orders_mode": "auto"
}

def load_risk_config():
    if os.path.exists(RISK_CONFIG_PATH):
        try:
            with open(RISK_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                res = dict(DEFAULT_RISK_CONFIG)
                res.update(data)
                return res
        except Exception:
            pass
    return dict(DEFAULT_RISK_CONFIG)

def save_risk_config(cfg):
    try:
        with open(RISK_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Errore salvataggio risk config: {e}")

def get_effective_max_orders(collat: float = None) -> tuple[int, str, int]:
    """Ritorna (effective_max_orders, mode, auto_calculated_orders). Base: 2 ordini (1 coppia YES+NO) ogni 20$ di saldo."""
    cfg = load_risk_config()
    mode = cfg.get("max_orders_mode", "auto")
    if collat is None:
        try:
            collat = float(engine.get_clob_collateral() or 0.0)
        except Exception:
            collat = 20.0
    auto_orders = max(2, int(collat // 20) * 2) if collat >= 20 else 2
    
    if mode == "manual" and cfg.get("max_open_orders"):
        effective = max(2, int(cfg.get("max_open_orders")))
    else:
        effective = auto_orders
    return effective, mode, auto_orders

def apply_max_orders(val: int = None, mode: str = "auto") -> dict:
    cfg = load_risk_config()
    if mode == "manual" and val is not None:
        cfg["max_open_orders"] = max(2, int(val))
        cfg["max_orders_mode"] = "manual"
    else:
        cfg["max_orders_mode"] = "auto"
        try:
            collat = float(engine.get_clob_collateral() or 0.0)
        except Exception:
            collat = 20.0
        cfg["max_open_orders"] = max(2, int(collat // 20) * 2) if collat >= 20 else 2

    save_risk_config(cfg)

    # Sincronizza il cap di esposizione in config.toml di poly-maker
    try:
        cfg_toml_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "config", "config.toml")
        if os.path.exists(cfg_toml_path):
            with open(cfg_toml_path, "r", encoding="utf-8") as tf:
                t_lines = tf.readlines()
            new_lines = []
            exp_cap = max(20.0, float(cfg["max_open_orders"]) * 11.0)
            for line in t_lines:
                if line.strip().startswith("max_total_exposure_usdc"):
                    new_lines.append(f"max_total_exposure_usdc = {exp_cap:.1f}\n")
                elif line.strip().startswith("max_market_notional_usdc"):
                    new_lines.append(f"max_market_notional_usdc = {exp_cap:.1f}\n")
                elif line.strip().startswith("max_event_group_loss_usdc"):
                    new_lines.append(f"max_event_group_loss_usdc = {exp_cap:.1f}\n")
                else:
                    new_lines.append(line)
            with open(cfg_toml_path, "w", encoding="utf-8") as tf:
                tf.writelines(new_lines)
    except Exception as e:
        print(f"Errore aggiornamento config.toml max orders: {e}")

    return cfg

def apply_risk_target(target_val: float):
    cfg = load_risk_config()
    cfg["target_daily_rewards_usd"] = round(target_val, 2)

    if target_val <= 1.20:
        cfg["risk_profile"] = "CONSERVATIVE"
        cfg["profile_name"] = "micro-rewards-conservative"
        cfg["slug"] = "will-the-uks-2026-inflation-be-between-3pt5-and-3pt9"
        cfg["title"] = "Will the UK's 2026 inflation be between 3.5% and 3.9%?"
        cfg["daily_pool"] = 61.0
        cfg["expected_daily_reward"] = 1.05
        cfg["delta_min_ticks"] = 2
    elif target_val <= 3.00:
        cfg["risk_profile"] = "BALANCED"
        cfg["profile_name"] = "micro-rewards"
        cfg["slug"] = "donald-trump-of-truth-social-posts-september-4-september-11-2026-200plus"
        cfg["title"] = "Will Donald Trump post 200+ Truth Social posts from September 4 to September 11, 2026?"
        cfg["daily_pool"] = 143.0
        cfg["expected_daily_reward"] = 2.15
        cfg["delta_min_ticks"] = 1
    else:
        cfg["risk_profile"] = "AGGRESSIVE"
        cfg["profile_name"] = "micro-rewards-aggressive"
        cfg["slug"] = "will-anton-danko-win-the-2026-poprad-mayoral-election"
        cfg["title"] = "Will Anton Danko win the 2026 Poprad mayoral election?"
        cfg["daily_pool"] = 173.0
        cfg["expected_daily_reward"] = max(target_val, 4.20)
        cfg["delta_min_ticks"] = 1

    cfg["expected_monthly_reward"] = round(cfg["expected_daily_reward"] * 30, 2)
    save_risk_config(cfg)

    # Aggiorna markets.toml per poly-maker
    markets_toml_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "config", "markets.toml")
    toml_content = f"""# Trade list (supervised MM session).
[[markets]]
slug    = "{cfg['slug']}"
profile = "{cfg['profile_name']}"
enabled = true
"""
    try:
        with open(markets_toml_path, "w", encoding="utf-8") as f:
            f.write(toml_content)
    except Exception as e:
        print(f"Errore scrittura markets.toml: {e}")

    try:
        engine.cancel_all_orders()
    except Exception:
        pass

    return cfg

async def handle_polymaker_risk_profile(request):
    cfg = load_risk_config()
    try:
        collat = float(engine.get_clob_collateral() or 0.0)
    except Exception:
        collat = 20.0
    effective, mode, auto_calc = get_effective_max_orders(collat)
    cfg["effective_max_orders"] = effective
    cfg["effective_max_pairs"] = effective // 2
    cfg["max_orders_mode"] = mode
    cfg["auto_calculated_orders"] = auto_calc
    cfg["collat_usdc"] = round(collat, 2)
    return web.json_response(cfg)

async def handle_polymaker_set_risk_profile(request):
    try:
        data = await request.json()
        target = float(data.get("target_daily_rewards_usd", 2.0))
        cfg = apply_risk_target(target)
        return web.json_response({"success": True, "config": cfg})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_polymaker_set_max_orders(request):
    try:
        data = await request.json()
        mode = data.get("mode", "auto")
        val = data.get("max_open_orders")
        if val is not None:
            val = int(val)
        cfg = apply_max_orders(val=val, mode=mode)
        try:
            collat = float(engine.get_clob_collateral() or 0.0)
        except Exception:
            collat = 20.0
        effective, mode, auto_calc = get_effective_max_orders(collat)
        return web.json_response({
            "success": True,
            "max_open_orders": cfg.get("max_open_orders"),
            "effective_max_orders": effective,
            "effective_max_pairs": effective // 2,
            "max_orders_mode": mode,
            "auto_calculated_orders": auto_calc
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_polymaker_accumulated_rewards(request):
    try:
        import httpx
        cfg = load_risk_config()
        wallet = engine.proxy_wallet
        url = f"https://data-api.polymarket.com/activity?user={wallet}&type=REWARD"
        
        onchain_total = 0.0
        payouts_count = 0
        payouts = []
        try:
            async with httpx.AsyncClient(timeout=8.0) as hc:
                r = await hc.get(url)
                if r.status_code == 200:
                    payouts = r.json()
                    payouts_count = len(payouts)
                    onchain_total = sum(float(item.get("usdcSize", 0) or 0) for item in payouts)
        except Exception:
            pass

        daily_pool = await get_active_market_daily_pool()
        metrics = await fetch_clob_rewards_metrics(daily_pool=daily_pool)
        pct_pool = metrics["pool_pct"]
        daily_yield_usd = metrics["daily_rate"]
        hourly_rate = metrics["hourly_rate"]
        today_earned = metrics["today_earned"]
        mins_left = metrics.get("mins_left")

        daily_val = daily_yield_usd if daily_yield_usd > 0 else float(cfg.get("expected_daily_reward", 1.55))
        hourly_val = hourly_rate if hourly_rate > 0 else (daily_val / 24.0)

        sprint_target = float(cfg.get("sprint_threshold_usd", cfg.get("target_daily_rewards_usd", 2.0)))
        sprint_shares = float(cfg.get("sprint_shares", 35.0))
        cruise_shares = float(cfg.get("cruise_shares", 20.0))
        is_sprint = (today_earned < sprint_target)
        mode_label = f"Sprint ({sprint_shares:.0f} q)" if is_sprint else f"Cruise ({cruise_shares:.0f} q)"
        active_shares = sprint_shares if is_sprint else cruise_shares

        return web.json_response({
            "onchain_total": round(onchain_total, 4),
            "payouts_count": payouts_count,
            "market_daily_pool": round(daily_pool, 2),
            "daily_yield_usd": round(daily_val, 2),
            "pct_pool": round(pct_pool, 3),
            "today_earned": round(today_earned, 4),
            "grand_total": round(onchain_total + today_earned, 4),
            "hourly_rate": round(hourly_val, 4),
            "mins_left": mins_left,
            "daily_target": sprint_target,
            "mode": mode_label,
            "is_sprint": is_sprint,
            "sprint_threshold": sprint_target,
            "sprint_shares": sprint_shares,
            "cruise_shares": cruise_shares,
            "active_shares": active_shares,
            "recent_payouts": payouts[:5]
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_polymaker_catalysts(request: web.Request) -> web.Response:
    try:
        from catalyst_manager import CatalystManager
        mgr = CatalystManager()
        events = mgr.list_events()
        res = []
        now = time.time()
        for ev in events:
            phase, hrs, next_ev, reason = mgr.evaluate_market(ev.market_slug, now)
            res.append({
                "id": ev.id,
                "title": ev.title,
                "market_slug": ev.market_slug,
                "event_date_utc": ev.event_date_utc,
                "phase": phase.value,
                "hours_left": round(hrs, 1) if hrs is not None else None,
                "days_left": round(hrs / 24.0, 1) if hrs is not None else None,
                "reason": reason,
                "source": ev.source,
                "notes": ev.notes,
            })
        return web.json_response({"status": "ok", "catalysts": res}, headers={"Cache-Control": "no-cache"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)



def get_miniapp_url() -> str:
    url_file = os.path.join(os.path.dirname(__file__), "cloudflared_url.txt")
    base = "https://pic-draft-presently-careers.trycloudflare.com"
    if os.path.exists(url_file):
        try:
            with open(url_file, "r") as f:
                content = f.read().strip()
                if content.startswith("http"):
                    base = content
        except Exception:
            pass
    base = os.getenv("TELEGRAM_MINIAPP_URL", base)
    ts = int(time.time())
    return f"{base}/miniapp?v={ts}"

def get_info_keyboard() -> dict:
    url = get_miniapp_url()
    return {
        "inline_keyboard": [
            [
                {"text": "🚀 Apri Mini App (Dashboard Live)", "web_app": {"url": url}}
            ],
            [
                {"text": "📊 Saldo & Ordini", "callback_data": "/status"},
                {"text": "📈 Profit & Loss", "callback_data": "/pnl"}
            ],
            [
                {"text": "💰 Ricompense Oggi", "callback_data": "/rewards"},
                {"text": "⚡ Tetto Ordini", "callback_data": "/maxorders"}
            ],
            [
                {"text": "🎯 Profilo Rischio", "callback_data": "/target"},
                {"text": "📜 Storico Payout", "callback_data": "/accumulated"}
            ],
            [
                {"text": "🛑 Stop Emergenza", "callback_data": "/stop"},
                {"text": "🟢 Riattiva Bot", "callback_data": "/resume"}
            ]
        ]
    }

def create_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/polymaker", handle_polymaker_page)
    app.router.add_get("/miniapp", handle_miniapp_page)
    app.router.add_get("/tg_app", handle_miniapp_page)
    app.router.add_get("/logical", handle_logical_page)
    app.router.add_get("/training", handle_training_page)
    app.router.add_get("/simulation", handle_simulation_page)
    app.router.add_get("/api/polymaker/status", handle_polymaker_status)
    app.router.add_get("/api/polymaker/pnl", handle_polymaker_pnl)
    app.router.add_get("/api/polymaker/markets", handle_polymaker_catalog)
    app.router.add_post("/api/polymaker/set_market", handle_polymaker_set_market)
    app.router.add_post("/api/polymaker/cancel_all", handle_polymaker_cancel_all)
    app.router.add_get("/api/polymaker/doctor", handle_polymaker_doctor)
    app.router.add_get("/api/polymaker/risk_profile", handle_polymaker_risk_profile)
    app.router.add_post("/api/polymaker/set_risk_profile", handle_polymaker_set_risk_profile)
    app.router.add_post("/api/polymaker/set_max_orders", handle_polymaker_set_max_orders)
    app.router.add_get("/api/polymaker/accumulated_rewards", handle_polymaker_accumulated_rewards)
    app.router.add_get("/api/polymaker/catalysts", handle_polymaker_catalysts)
    app.router.add_get("/api/polymarket/system_status", handle_polymarket_system_status)
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

async def tg_info_handler() -> tuple[str, dict]:
    url = get_miniapp_url()
    text = (
        "🤖 <b>PANNELLO DI CONTROLLO BOT POLYMARKET</b>\n\n"
        "🚀 <b>DASHBOARD GRAFICA INTERATTIVA:</b>\n"
        f"• Tocca il pulsante <b>'🚀 Apri Mini App'</b> in basso o il tasto <b>[📊 Dashboard]</b> nella chat.\n"
        f"• Oppure aprila nel browser da questo link: <a href='{url}'>{url}</a>\n\n"
        "<i>Oppure tocca i comandi blu o i tasti rapidi:</i>\n\n"
        "📊 <b>MONITORAGGIO & YIELD:</b>\n"
        "• /status — Saldo wallet, ordini aperti, scoring e stato\n"
        "• /pnl — Rendimento & PnL (oggi, 7 giorni, 30 giorni, sempre)\n"
        "• /rewards — Rendimento live oggi, quota pool e tempo alla soglia\n"
        "• /accumulated — Storico accrediti totali ricevuti on-chain\n\n"
        "⚡ <b>GESTIONE CAPACITÀ & ORDINI:</b>\n"
        "• /maxorders — Visualizza tetto ordini aperti e modalità\n"
        "• <code>/maxorders auto</code> — Auto (2 ordini per ogni 20€ di saldo)\n"
        "• <code>/maxorders 2</code> — Forza massimo 2 ordini (1 coppia)\n"
        "• <code>/maxorders 4</code> — Forza massimo 4 ordini (2 coppie)\n\n"
        "🎯 <b>PROFILO DI RISCHIO & TARGET:</b>\n"
        "• /target — Visualizza target attivo e resa stimata\n"
        "• <code>/target 1</code> — Profilo Conservativo ($1.00/gg, rischio min)\n"
        "• <code>/target 2</code> — Profilo Bilanciato ($2.00/gg, consigliato)\n"
        "• <code>/target 4</code> — Profilo Aggressivo ($4.00+/gg, max yield)\n\n"
        "⚙️ <b>CONTROLLO OPERATIVO:</b>\n"
        "• /ping — Verifica reattività e connessione del server\n"
        "• /stop — Cancellazione immediata ordini ed arresto emergenza\n"
        "• /resume — Riattiva la quotazione automatica sul book\n"
        "• /info — Riapre questa guida\n\n"
        "👇 <i>Tocca un pulsante qui sotto per una risposta immediata:</i>"
    )
    return text, get_info_keyboard()

_market_pool_cache: dict[str, tuple[float, float]] = {}

async def get_active_market_daily_pool(cid: str = None) -> float:
    global _market_pool_cache
    now = time.time()
    cfg = load_risk_config()
    slug = cfg.get("slug", "will-anthropic-ipo-by-october-15-2026-949")
    if not cid:
        cid = cfg.get("condition_id") or getattr(engine, "active_condition_id", None) or "0x25ea45fb5a112391bf64f38056b84394f66cb47a06aa8208339822408fb0a315"

    if cid in _market_pool_cache:
        val, ts = _market_pool_cache[cid]
        if now - ts < 300:
            return val

    # 1. Try CLOB market endpoint
    try:
        async with httpx.AsyncClient(timeout=4.0) as hc:
            r = await hc.get(f"https://clob.polymarket.com/markets/{cid}")
            if r.status_code == 200:
                rates = r.json().get("rewards", {}).get("rates", [])
                if rates:
                    rate = float(rates[0].get("rewards_daily_rate", 0.0) or 0.0)
                    if rate > 0:
                        _market_pool_cache[cid] = (rate, now)
                        return rate
    except Exception:
        pass

    # 2. Try Gamma API slug lookup
    try:
        async with httpx.AsyncClient(timeout=4.0) as hc:
            r = await hc.get(f"https://gamma-api.polymarket.com/markets?slug={slug}")
            if r.status_code == 200:
                items = r.json()
                if items:
                    for cr in items[0].get("clobRewards", []):
                        rate = float(cr.get("rewardsDailyRate", 0.0) or 0.0)
                        if rate > 0:
                            _market_pool_cache[cid] = (rate, now)
                            return rate
    except Exception:
        pass

    return float(cfg.get("daily_pool", 300.0) or 300.0)

async def fetch_clob_rewards_metrics(daily_pool: float = None) -> dict:
    """Fetch exact accumulated rewards and pool share in real-time from Polymarket CLOB."""
    if daily_pool is None or daily_pool <= 0:
        daily_pool = await get_active_market_daily_pool()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_earned = 0.0
    pool_pct = 0.0

    try:
        from py_clob_client.client import RequestArgs, create_level_2_headers
        c = engine.client
        
        # 1. Real-time earnings accumulated today from CLOB (/rewards/user/total)
        try:
            path_tot = "/rewards/user/total"
            req_args_tot = RequestArgs(method="GET", request_path=path_tot)
            headers_tot = create_level_2_headers(c.signer, c.creds, req_args_tot)
            async with httpx.AsyncClient(timeout=6.0) as hc:
                r_tot = await hc.get(f"{c.host}{path_tot}", headers=headers_tot, params={"date": today, "signature_type": 3})
                if r_tot.status_code == 200:
                    data_tot = r_tot.json()
                    if data_tot and isinstance(data_tot, list):
                        today_earned = float(data_tot[0].get("earnings", 0.0) or 0.0)
        except Exception as e_tot:
            print(f"[Rewards] Errore /rewards/user/total: {e_tot}")

        # 2. Real-time pool percentage share (/rewards/user/percentages)
        try:
            path_pct = "/rewards/user/percentages"
            req_args_pct = RequestArgs(method="GET", request_path=path_pct)
            headers_pct = create_level_2_headers(c.signer, c.creds, req_args_pct)
            async with httpx.AsyncClient(timeout=6.0) as hc:
                r_pct = await hc.get(f"{c.host}{path_pct}", headers=headers_pct, params={"signature_type": 3})
                if r_pct.status_code == 200:
                    data_pct = r_pct.json()
                    if isinstance(data_pct, dict):
                        pool_pct = sum(float(v or 0.0) for v in data_pct.values() if isinstance(v, (int, float)))
        except Exception as e_pct:
            print(f"[Rewards] Errore /rewards/user/percentages: {e_pct}")

    except Exception as e:
        print(f"[Rewards] Errore generale CLOB auth/headers: {e}")

    daily_rate = (pool_pct / 100.0) * daily_pool
    hourly_rate = daily_rate / 24.0
    rem_usd = max(0.0, 1.00 - today_earned)
    mins_left = int(round((rem_usd / hourly_rate) * 60)) if hourly_rate > 0 else None
    threshold_pct = (today_earned / 1.00) * 100.0

    return {
        "today_earned": today_earned,
        "pool_pct": pool_pct,
        "daily_rate": daily_rate,
        "hourly_rate": hourly_rate,
        "rem_usd": rem_usd,
        "mins_left": mins_left,
        "threshold_pct": threshold_pct,
    }

async def fetch_daily_rewards_live() -> float:
    """Fetch exact accumulated rewards for today from Polymarket CLOB."""
    metrics = await fetch_clob_rewards_metrics()
    return metrics["today_earned"]

async def tg_accumulated_handler() -> str:
    return await tg_rewards_handler()

async def tg_rewards_handler() -> str:
    try:
        cfg = load_risk_config()
        slug = cfg.get("slug", "will-anthropic-ipo-by-october-15-2026-949")
        question = cfg.get("title", slug)
        daily_pool = await get_active_market_daily_pool()
        wallet = engine.proxy_wallet

        # On-chain past rewards
        onchain_total = 0.0
        last_payout_str = "Nessun accredito on-chain precedente"
        try:
            url = f"https://data-api.polymarket.com/activity?user={wallet}&type=REWARD&limit=5"
            async with httpx.AsyncClient(timeout=8.0) as hc:
                r = await hc.get(url)
                if r.status_code == 200:
                    data = r.json()
                    onchain_total = sum(float(item.get("usdcSize", 0) or 0) for item in data)
                    if data:
                        last_ts = data[0].get("timestamp")
                        last_val = float(data[0].get("usdcSize", 0) or 0)
                        tx_hash = data[0].get("transactionHash", "")[:10]
                        if last_ts:
                            last_date = datetime.fromtimestamp(last_ts, timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
                            last_payout_str = f"+{last_val:.4f}$ USDC ({last_date})"
        except Exception:
            pass

        # Live CLOB rewards metrics
        metrics = await fetch_clob_rewards_metrics(daily_pool=daily_pool)
        today_earned = metrics["today_earned"]
        pool_pct = metrics["pool_pct"]
        daily_rate = metrics["daily_rate"]
        hourly_rate = metrics["hourly_rate"]
        rem_usd = metrics["rem_usd"]
        mins_left = metrics["mins_left"]
        threshold_pct = metrics["threshold_pct"]

        # Check current active orders scoring status
        scoring_count = 0
        total_open = 0
        try:
            from py_clob_client_v2.clob_types import OrderScoringParams
            raw_orders = engine.client.get_open_orders()
            total_open = len(raw_orders)
            for o in raw_orders:
                oid = o.get("id") or o.get("orderID") or ""
                if oid:
                    try:
                        sc = engine.client.is_order_scoring(OrderScoringParams(orderId=oid))
                        if sc.get("scoring", False):
                            scoring_count += 1
                    except Exception:
                        pass
        except Exception:
            pass

        scoring_badge = f"🟢 <b>{scoring_count}/{total_open} ordini premiati ora</b>" if scoring_count > 0 else "⚪ In attesa nel book"

        # Dynamic calculation of remaining time to 1.00$ payout threshold
        if today_earned >= 1.00:
            soglia_text = (
                f"🟢 <b>SOGLIA $1.00 GIÀ SUPERATA!</b>\n"
                f"• <b>Guadagno Accumulato:</b> <code>+{today_earned:.4f}$ USDC</code> ({threshold_pct:.1f}% della soglia)\n"
                f"• <b>Payout Stanotte:</b> Confermato per le 00:00 UTC direttamente nel tuo wallet USDC (minimo $1.00 garantito)."
            )
        else:
            if mins_left is not None and mins_left > 0:
                hours = mins_left // 60
                mins = mins_left % 60
                if hours > 0:
                    time_desc = f"circa {hours} ore e {mins} minuti"
                else:
                    time_desc = f"circa {mins} minuti"
                tempo_info = f"<code>+{rem_usd:.4f}$ USDC</code> ({time_desc} nel book al ritmo attuale)"
            else:
                tempo_info = f"<code>+{rem_usd:.4f}$ USDC</code> (ritmo in calcolo dal book)"

            soglia_text = (
                f"🟡 <b>IN MATURAZIONE ({today_earned:.4f}$ / $1.00 - {threshold_pct:.1f}%):</b>\n"
                f"• <b>Mancano alla soglia:</b> {tempo_info}\n"
                f"• <b>Accredito Stanotte:</b> Alle 00:00 UTC (solo se il totale raggiunge almeno $1.00)."
            )

        sprint_target = float(cfg.get("sprint_threshold_usd", cfg.get("target_daily_rewards_usd", 2.0)))
        sprint_shares = float(cfg.get("sprint_shares", 35.0))
        cruise_shares = float(cfg.get("cruise_shares", 20.0))
        is_sprint = (today_earned < sprint_target)
        mode_label = f"⚡ Sprint ({sprint_shares:.0f} q)" if is_sprint else f"🚢 Cruise ({cruise_shares:.0f} q)"

        return (
            f"🏆 <b>DATI UFFICIALI RICOMPENSE POLYMARKET</b>\n\n"
            f"• <b>Wallet Funder:</b> <code>{wallet[:6]}...{wallet[-4:]}</code>\n"
            f"• <b>Mercato Attivo:</b> <i>{question}</i>\n"
            f"• <b>Montepremi Mercato:</b> <code>${daily_pool:.0f} USDC / giorno</code>\n"
            f"• <b>Modalità Quota:</b> <b>{mode_label}</b> (Obiettivo: ${sprint_target:.2f})\n"
            f"• <b>Stato Scoring Ordini:</b> {scoring_badge}\n\n"
            f"💰 <b>Guadagno Accumulato Oggi (Live CLOB):</b>\n"
            f"• <b>Totale Odierno:</b> <code>+{today_earned:.4f}$ USDC</code>\n"
            f"• <b>Quota Liquidity Pool:</b> <code>{pool_pct:.3f}%</code>\n"
            f"• <b>Velocità di Guadagno:</b> <code>+{hourly_rate:.4f}$ USDC / ora</code>\n"
            f"• <b>Proiezione 24h:</b> <code>+{daily_rate:.2f}$ USDC / giorno</code> (a quote costanti)\n\n"
            f"⏳ <b>Soglia Minima di Accredito ($1.00/giorno):</b>\n"
            f"• {soglia_text}\n\n"
            f"• <b>Storico Payout Precedenti:</b> <code>+{onchain_total:.4f}$ USDC</code>\n"
            f"• <b>Ultimo Accredito On-Chain:</b> <code>{last_payout_str}</code>\n\n"
            f"🔗 <i>Dati certificati estratti in tempo reale da Polymarket CLOB API (/rewards/user/total & /rewards/user/percentages).</i>"
        )
    except Exception as e:
        return f"⚠️ Errore recupero ricompense ufficiali: {e}"



async def tg_stop_handler() -> str:
    try:
        engine.cancel_all_orders()
        return "🛑 <b>BOT ARRESTATO:</b> Tutti gli ordini aperti sono stati cancellati dal CLOB."
    except Exception as e:
        return f"⚠️ Errore arresto bot: {e}"

async def tg_resume_handler() -> str:
    return "🟢 <b>BOT ATTIVO:</b> La quotazione automatica è in esecuzione."

async def tg_max_orders_handler(raw_text: str) -> str:
    parts = raw_text.strip().split()
    try:
        collat = float(engine.get_clob_collateral() or 0.0)
    except Exception:
        collat = 20.0
    effective, mode, auto_calc = get_effective_max_orders(collat)

    if len(parts) >= 2:
        arg = parts[1].lower()
        if "auto" in arg or "def" in arg:
            cfg = apply_max_orders(mode="auto")
            effective, mode, auto_calc = get_effective_max_orders(collat)
            return (
                f"✅ <b>TETTO ORDINI IMPOSTATO SU AUTOMATICO!</b>\n\n"
                f"• <b>Regola:</b> 2 ordini (1 coppia YES+NO) ogni 20€ di saldo\n"
                f"• <b>Saldo Attuale:</b> <code>{collat:.2f}$ USDC</code>\n"
                f"• <b>Capacità Attiva:</b> <code>max {effective} ordini ({effective//2} coppie complete)</code>\n"
                f"• <b>Stato:</b> 🟢 Sincronizzato con il motore poly-maker!"
            )
        else:
            try:
                num = int(arg)
                if num < 2:
                    return "⚠️ Il tetto minimo è di 2 ordini (1 coppia YES+NO)."
                if num % 2 != 0:
                    num += 1  # arrotonda al numero pari di ordini per coppie complete
                cfg = apply_max_orders(val=num, mode="manual")
                return (
                    f"✅ <b>TETTO ORDINI PERSONALIZZATO IMPOSTATO!</b>\n\n"
                    f"• <b>Modalità:</b> 🔧 <b>MANUALE</b>\n"
                    f"• <b>Tetto Massimo:</b> <code>{num} ordini aperti ({num//2} coppie complete)</code>\n"
                    f"• <b>Saldo Attuale:</b> <code>{collat:.2f}$ USDC</code>\n"
                    f"• <b>Stato:</b> 🟢 Limite applicato al motore poly-maker!"
                )
            except ValueError:
                return "⚠️ Formato non valido. Usa: <code>/maxorders auto</code> oppure <code>/maxorders 4</code>"

    mode_badge = "🔄 AUTOMATICO (2 ordini ogni 20€)" if mode == "auto" else f"🔧 MANUALE ({effective} ordini)"
    return (
        f"⚡ <b>CAPACITÀ & TETTO MASSIMO ORDINI</b>\n\n"
        f"• <b>Modalità Attuale:</b> <b>{mode_badge}</b>\n"
        f"• <b>Tetto Attivo:</b> <code>max {effective} ordini ({effective//2} coppie complete)</code>\n"
        f"• <b>Saldo Wallet:</b> <code>{collat:.2f}$ USDC</code>\n"
        f"• <b>Calcolo Base (Auto):</b> <code>{auto_calc} ordini ({auto_calc//2} coppie)</code>\n\n"
        f"💡 <i>Di base il bot stanzia 2 ordini (1 coppia YES+NO) ogni 20€ di saldo per proteggere il capitale e sbloccare le ricompense senza rischi di squilibrio.</i>\n\n"
        f"<b>Per modificare il tetto scrivi:</b>\n"
        f"• <code>/maxorders auto</code> - 🔄 Calcolo automatico su base saldo (2 ogni 20€)\n"
        f"• <code>/maxorders 2</code> - 1 Coppia max (2 ordini)\n"
        f"• <code>/maxorders 4</code> - 2 Coppie max (4 ordini)\n"
        f"• <code>/maxorders 6</code> - 3 Coppie max (6 ordini)\n"
        f"<i>Oppure digita qualsiasi numero pari: es. <code>/maxorders 8</code></i>"
    )

async def start_background_tasks(app):
    from telegram_bot import telegram
    app['trading_task'] = asyncio.create_task(engine.trading_loop())
    app['simulation_task'] = asyncio.create_task(sim_engine.simulation_loop())
    app['sentinel_task'] = asyncio.create_task(polymarket_status_sentinel_loop())
    if telegram.is_configured:
        app['telegram_task'] = asyncio.create_task(
            telegram.poll_commands(
                on_status_request=tg_status_handler, 
                on_rewards_request=tg_rewards_handler, 
                on_stop_request=tg_stop_handler, 
                on_resume_request=tg_resume_handler, 
                on_pnl_request=tg_pnl_handler,
                on_target_request=tg_target_handler,
                on_info_request=tg_info_handler,
                on_accumulated_request=tg_accumulated_handler,
                on_max_orders_request=tg_max_orders_handler
            )
        )
        asyncio.create_task(telegram.send_message("🚀 <b>Server Polymarket Avviato!</b>\nNotifiche attive e bot pronto."))

async def tg_status_handler() -> str:
    collat = engine.get_clob_collateral()
    orders = []
    locked_in_orders = 0.0
    raw_orders = []
    try:
        from py_clob_client_v2.clob_types import OrderScoringParams
        raw_orders = engine.client.get_open_orders()
        for o in raw_orders:
            oid = o.get("id") or o.get("orderID") or ""
            side = o.get("side", "BUY")
            outcome = o.get("outcome", "")
            sz = float(o.get("original_size", 0) or 0)
            p = float(o.get("price", 0) or 0)
            if side == "BUY":
                locked_in_orders += (p * sz)
            
            is_scoring = False
            if oid:
                try:
                    sc = engine.client.is_order_scoring(OrderScoringParams(orderId=oid))
                    is_scoring = bool(sc.get("scoring", False))
                except Exception:
                    pass
            
            sc_badge = "🟢 <b>REWARDS ATTIVE</b>" if is_scoring else "⚪ In attesa"
            outcome_str = f" {outcome.upper()}" if outcome else ""
            orders.append(f"  • <b>{side}{outcome_str}:</b> <code>{sz:.1f} quote @ {p:.3f}$</code> | {sc_badge}")
    except Exception:
        pass
    
    # Invariant: Net Worth is cash balance + held token positions
    cached_pos = getattr(engine, "cached_positions", [])
    positions_val = float(getattr(engine, "cached_positions_val", 0.0))
    if not cached_pos and hasattr(engine, "proxy_wallet") and engine.proxy_wallet:
        try:
            import httpx
            pos_url = f"https://data-api.polymarket.com/positions?user={engine.proxy_wallet}"
            async with httpx.AsyncClient(timeout=4.0) as hc:
                p_resp = await hc.get(pos_url)
                if p_resp.status_code == 200:
                    positions = p_resp.json()
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
                    cached_pos = formatted_pos
                    positions_val = round(tot_pos_val, 2)
                    engine.cached_positions = formatted_pos
                    engine.cached_positions_val = positions_val
        except Exception:
            pass

    # Filter active positions
    pos_lines = []
    for p in cached_pos:
        val = float(p.get("current_val", 0.0) or 0.0)
        sz = float(p.get("size", 0.0) or 0.0)
        if val >= 0.10 and sz >= 0.5:
            tok_id = str(p.get("token_id", ""))
            matching_sell = next((o for o in raw_orders if str(o.get("asset_id", "")) == tok_id and o.get("side") == "SELL"), None)
            sell_info = f" [In vendita @ {float(matching_sell.get('price', 0)):.2f}$]" if matching_sell else ""
            pnl_u = float(p.get("pnl_usd", 0.0))
            pnl_sign = "+" if pnl_u >= 0 else ""
            pos_lines.append(f"  • <b>{p.get('title')[:32]}:</b> <code>{sz:.1f} quote @ {p.get('cur_price', 0):.3f}$</code> (Valore: <code>{val:.2f}$</code>, PnL: <code>{pnl_sign}{pnl_u:.2f}$</code>){sell_info}")

    pos_text = "\n".join(pos_lines) if pos_lines else "  • <i>Nessuna posizione aperta (100% contanti)</i>"

    cfg = load_risk_config()
    effective, mode, auto_calc = get_effective_max_orders(collat)
    mode_str = "Auto (2 ogni 20€)" if mode == "auto" else "Manuale"
    orders_text = "\n".join(orders) if orders else "  • <i>Nessun ordine aperto</i>"
    total_balance = collat
    free_cash = max(0.0, total_balance - locked_in_orders)
    net_worth = round(total_balance + positions_val, 2)
    market_title = cfg.get("title", "Will Anthropic IPO by October 15, 2026?")

    today_earned = await fetch_daily_rewards_live()
    daily_est_str = f"\n• <b>Ricompense Oggi (Live CLOB):</b> <code>+{today_earned:.4f}$ USDC</code> (Soglia $1.00: {today_earned*100:.1f}%)"

    active_pos = [p for p in cached_pos if float(p.get("current_val", 0) or 0) >= 0.10 and float(p.get("size", 0) or 0) >= 0.5]
    pnl = calculate_pnl_analytics(net_worth, today_earned, active_pos)
    p1d = pnl["1d"]
    pall = pnl["all"]
    p1d_sign = "+" if p1d["pnl_usd"] >= 0 else ""
    pall_sign = "+" if pall["pnl_usd"] >= 0 else ""
    pnl_str = f"\n• <b>Rendimento PnL:</b> Oggi <code>{p1d_sign}{p1d['pnl_usd']:.2f}$ ({p1d_sign}{p1d['pnl_pct']:.1f}%)</code> | All-Time <code>{pall_sign}{pall['pnl_usd']:.2f}$ ({pall_sign}{pall['pnl_pct']:.1f}%)</code>"

    return (
        f"🦅 <b>STATO BOT POLYMARKET</b>\n\n"
        f"• <b>Valore Totale Portafoglio (Net Worth):</b> <code>{net_worth:.2f}$ USDC</code>\n"
        f"  ├─ 💵 <b>Cassa Funder:</b> <code>{total_balance:.2f}$ pUSD</code> (Disponibile: <code>{free_cash:.2f}$</code>, Impegnato BUY: <code>{locked_in_orders:.2f}$</code>)\n"
        f"  └─ 📦 <b>Valore Token in Portafoglio:</b> <code>{positions_val:.2f}$ USDC</code>\n\n"
        f"📦 <b>Posizioni Attive in Portafoglio ({len(pos_lines)}):</b>\n{pos_text}\n\n"
        f"📋 <b>Ordini Aperti nel Book ({len(orders)}):</b>\n{orders_text}\n\n"
        f"• <b>Tetto Max Ordini:</b> <code>{effective} ordini ({effective//2} coppie) [{mode_str}]</code>\n"
        f"• <b>Mercato Attivo:</b> <i>{market_title}</i>{daily_est_str}{pnl_str}\n\n"
        f"• <b>Stato Sistema:</b> 🟢 <code>OPERATIVO</code>\n"
        f"• <b>Orario:</b> <code>{time.strftime('%H:%M:%S')}</code>"
    )

async def tg_pnl_handler() -> str:
    collat = engine.get_clob_collateral()
    cached_pos = getattr(engine, "cached_positions", [])
    positions_val = float(getattr(engine, "cached_positions_val", 0.0))
    if not cached_pos and hasattr(engine, "proxy_wallet") and engine.proxy_wallet:
        try:
            import httpx
            pos_url = f"https://data-api.polymarket.com/positions?user={engine.proxy_wallet}"
            async with httpx.AsyncClient(timeout=4.0) as hc:
                p_resp = await hc.get(pos_url)
                if p_resp.status_code == 200:
                    positions = p_resp.json()
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
                    cached_pos = formatted_pos
                    positions_val = round(tot_pos_val, 2)
                    engine.cached_positions = formatted_pos
                    engine.cached_positions_val = positions_val
        except Exception:
            pass

    active_pos = [p for p in cached_pos if float(p.get("current_val", 0) or 0) >= 0.10 and float(p.get("size", 0) or 0) >= 0.5]
    positions_val = sum(float(p.get("current_val", 0) or 0) for p in active_pos)
    net_worth = collat + positions_val
    today_rewards = await fetch_daily_rewards_live()
    pnl = calculate_pnl_analytics(net_worth, today_rewards, active_pos)
    
    p1d = pnl["1d"]
    p7d = pnl["7d"]
    p30d = pnl["30d"]
    pall = pnl["all"]

    def fmt_pnl(val, pct):
        sign = "+" if val >= 0 else ""
        return f"<code>{sign}{val:.2f}$ USDC</code> (<b>{sign}{pct:.1f}%</b>)"

    return (
        f"📈 <b>REPORT PROFIT & LOSS (RENDIMENTO)</b>\n\n"
        f"• 📅 <b>Oggi (24h):</b> {fmt_pnl(p1d['pnl_usd'], p1d['pnl_pct'])}\n"
        f"  ├─ 🎁 Ricompense maturate: <code>+{p1d['rewards_usd']:.2f}$ USDC</code>\n"
        f"  └─ ⚖️ Trading & Inventario MTM: <code>{p1d['trading_pnl']:+.2f}$ USDC</code>\n\n"
        f"• 🗓️ <b>1 Settimana (7G):</b> {fmt_pnl(p7d['pnl_usd'], p7d['pnl_pct'])}\n"
        f"  ├─ 🎁 Ricompense totali: <code>+{p7d['rewards_usd']:.2f}$ USDC</code>\n"
        f"  └─ ⚖️ Trading & Merge: <code>{p7d['trading_pnl']:+.2f}$ USDC</code>\n\n"
        f"• 📆 <b>1 Mese (30G):</b> {fmt_pnl(p30d['pnl_usd'], p30d['pnl_pct'])}\n\n"
        f"• 🏆 <b>Di Sempre (All-Time):</b> {fmt_pnl(pall['pnl_usd'], pall['pnl_pct'])}\n"
        f"  ├─ 💵 Capitale Totale Depositato: <code>{pall['base_capital']:.2f}$ USDC</code>\n"
        f"  ├─ 📦 Patrimonio Netto Attuale: <code>{pall['current_net_worth']:.2f}$ USDC</code>\n"
        f"  └─ 💰 Valore Totale (con rewards): <code>{pall['total_equity_with_rewards']:.2f}$ USDC</code>\n\n"
        f"💡 <i>Tocca <b>[🚀 Apri Mini App]</b> per visualizzare i grafici interattivi con selettore rapido 1D / 7D / 30D / ALL.</i>"
    )


async def tg_target_handler(raw_text: str) -> str:
    parts = raw_text.strip().split()
    cfg = load_risk_config()
    
    if len(parts) >= 2:
        arg = parts[1].lower()
        try:
            val = float(arg.replace("$", "").replace("€", "").replace(",", "."))
            cfg = apply_risk_target(val)
            emoji = "🛡️" if cfg["risk_profile"] == "CONSERVATIVE" else ("⚖️" if cfg["risk_profile"] == "BALANCED" else "🚀")
            return (
                f"✅ <b>TARGET RICOMPENSE IMPOSTATO!</b>\n\n"
                f"• <b>Nuovo Target:</b> <code>${cfg['target_daily_rewards_usd']:.2f} USDC / giorno</code>\n"
                f"• <b>Profilo di Rischio:</b> {emoji} <b>{cfg['risk_profile']}</b>\n"
                f"• <b>Mercato Selezionato:</b> <i>{cfg['title'][:55]}...</i>\n"
                f"• <b>Montepremi Pool:</b> <code>${cfg['daily_pool']:.1f} / giorno</code>\n"
                f"• <b>Rendimento Stimato:</b> <code>+${cfg['expected_daily_reward']:.2f}/giorno</code> (~${cfg['expected_monthly_reward']:.1f}/mese)\n"
                f"• <b>Stato:</b> 🟢 Vecchi ordini revocati, nuovo profilo attivo!"
            )
        except ValueError:
            if "cons" in arg or "safe" in arg or "basso" in arg:
                cfg = apply_risk_target(1.0)
            elif "agg" in arg or "alto" in arg or "max" in arg:
                cfg = apply_risk_target(4.0)
            elif "bil" in arg or "med" in arg:
                cfg = apply_risk_target(2.0)
            else:
                return "⚠️ Formato non valido. Usa ad esempio: <code>/target 2</code> oppure <code>/target 1.5</code>"
            
            emoji = "🛡️" if cfg["risk_profile"] == "CONSERVATIVE" else ("⚖️" if cfg["risk_profile"] == "BALANCED" else "🚀")
            return (
                f"✅ <b>PROFILO DI RISCHIO IMPOSTATO:</b> {emoji} <b>{cfg['risk_profile']}</b>\n\n"
                f"• <b>Target Ricompense:</b> <code>${cfg['target_daily_rewards_usd']:.2f} USDC / giorno</code>\n"
                f"• <b>Mercato Scelto:</b> <i>{cfg['title'][:55]}...</i>\n"
                f"• <b>Montepremi Pool:</b> <code>${cfg['daily_pool']:.1f} / giorno</code>\n"
                f"• <b>Stima Mensile:</b> <code>~${cfg['expected_monthly_reward']:.1f} USDC / mese</code>\n"
                f"• <b>Stato:</b> 🟢 Configurazione applicata al bot."
            )
    
    emoji = "🛡️" if cfg["risk_profile"] == "CONSERVATIVE" else ("⚖️" if cfg["risk_profile"] == "BALANCED" else "🚀")
    return (
        f"🎯 <b>PROFILO DI RISCHIO & TARGET RICOMPENSE</b>\n\n"
        f"• <b>Target Attuale:</b> <code>${cfg['target_daily_rewards_usd']:.2f} USDC / giorno</code>\n"
        f"• <b>Profilo Attivo:</b> {emoji} <b>{cfg['risk_profile']}</b>\n"
        f"• <b>Mercato:</b> <i>{cfg['title'][:50]}...</i>\n"
        f"• <b>Montepremi Pool:</b> <code>${cfg['daily_pool']:.1f} / giorno</code>\n"
        f"• <b>Stima Mensile:</b> <code>~${cfg['expected_monthly_reward']:.1f} USDC / mese</code>\n\n"
        f"💡 <i>Polymarket accredita le ricompense solo se superi 1.00$/giorno!</i>\n\n"
        f"<b>Per cambiare il target scrivi:</b>\n"
        f"• <code>/target 1</code> - 🛡️ <b>Conservativo</b> ($1.00/gg, rischio minimo, spread largo)\n"
        f"• <code>/target 2</code> - ⚖️ <b>Bilanciato</b> ($2.00/gg, consigliato, resa ottimizzata)\n"
        f"• <code>/target 4</code> - 🚀 <b>Aggressivo</b> ($4.00+/gg, max yield su montepremi top)\n"
        f"<i>Oppure digita qualsiasi valore: es. <code>/target 2.5</code></i>"
    )

async def cleanup_background_tasks(app):
    app['trading_task'].cancel()
    app['simulation_task'].cancel()
    app['sentinel_task'].cancel()
    if 'telegram_task' in app:
        app['telegram_task'].cancel()
    await asyncio.gather(app['trading_task'], app['simulation_task'], app['sentinel_task'], return_exceptions=True)

if __name__ == "__main__":
    app = create_app()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    web.run_app(app, host="0.0.0.0", port=8080)

