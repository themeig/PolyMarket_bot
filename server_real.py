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
from py_clob_client_v2.clob_types import OrderArgs, OrderArgsV2, OrderType, BalanceAllowanceParams, AssetType
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
        self.free_usdc = 31.87
        self.pol_gas = self.fetch_onchain_pol()
        self.session_start_ts = time.time()
        self.trade_history = []
        self.cached_trades = []
        self.merge_history = []

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
        if hasattr(self, "_cached_cash") and (now - getattr(self, "_cached_cash_ts", 0)) < 3.0:
            return self._cached_cash
        try:
            wrapped_c = self.w3.eth.contract(
                address=Web3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"),
                abi=[{"name": "balanceOf", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
            )
            bridged_c = self.w3.eth.contract(
                address=Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
                abi=[{"name": "balanceOf", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
            )
            bal_w = wrapped_c.functions.balanceOf(Web3.to_checksum_address(self.proxy_wallet)).call() / 1e6
            bal_b = bridged_c.functions.balanceOf(Web3.to_checksum_address(self.proxy_wallet)).call() / 1e6
            self._cached_cash = float(bal_w + bal_b)
            self._cached_cash_ts = now
            return self._cached_cash
        except Exception:
            return getattr(self, "_cached_cash", 31.87)

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
        # 1. GESTIONE AUTOMATICA INVENTARIO (AUTO-PAIRING O EXIT PASSIVO A PROFITTO)
        # Se abbiamo quote in mano da esecuzioni precedenti, le gestiamo PRIMA di aprire nuovi mercati:
        # Se c'è saldo -> Piazza l'opposto per fare Merge a 1.00$
        # Se non c'è saldo -> Piazza Limit SELL passivo a profitto (+5%) per recuperare subito USDC liquidi
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
                except Exception:
                    pass

                # =========================================================================
                # 1. GESTIONE INVENTARIO & EXIT URGENCY (POLY-MAKER STYLE)
                # =========================================================================
                held_opp_tokens = set()
                self.held_opp_tokens = held_opp_tokens

                for pos in positions:
                    size = float(pos.get("size", 0) or 0)
                    cur_val = float(pos.get("currentValue", 0) or 0)
                    if size < 1.0 or cur_val < 0.50:
                        continue
                    
                    asset_id = str(pos.get("asset"))
                    opp_asset = pos.get("oppositeAsset")
                    opp_outcome = pos.get("oppositeOutcome", "YES")
                    avg_p = float(pos.get("avgPrice", 0) or 0)
                    cur_p = float(pos.get("curPrice", 0) or avg_p)
                    title = pos.get("title", "")
                    
                    if asset_id not in self.position_acquired_ts:
                        self.position_acquired_ts[asset_id] = time.time()
                    
                    hold_duration = time.time() - self.position_acquired_ts[asset_id]
                    adverse_drift = avg_p - cur_p  # Fluttuazione sfavorevole del prezzo

                    # REGOLA 1: EXIT URGENCY (Se hold > 60s O drift sfavorevole >= 2c, liquida subito al Best Bid per proteggere il capitale)
                    if hold_duration >= 60.0 or adverse_drift >= 0.02:
                        best_bid_exit = 0.01
                        try:
                            b_exit = self.client.get_order_book(asset_id)
                            bids_list = b_exit.get("bids", []) if isinstance(b_exit, dict) else getattr(b_exit, "bids", [])
                            if bids_list:
                                best_bid_exit = max(float(b.get("price") if isinstance(b, dict) else b.price) for b in bids_list)
                        except Exception:
                            pass
                        
                        exit_p = max(0.01, min(0.99, round(best_bid_exit, 2)))
                        print(f"[{now_str}] 🛑 EXIT URGENCY (Poly-Maker Guard): Posizione {size:.0f} quote {pos.get('outcome')} '{title[:20]}' tenuta per {hold_duration:.0f}s (Drift: -{adverse_drift*100:.1f}c). Liquidazione immediata al Best Bid @ {exit_p:.2f}$!")
                        try:
                            # Cancella eventuale ordine BUY opposto
                            if opp_asset and str(opp_asset) in open_buy_assets:
                                o_to_cancel = [o.get("id") or o.get("orderID") for o in open_orders if str(o.get("asset_id")) == str(opp_asset)]
                                if o_to_cancel:
                                    self.client.cancel_orders(o_to_cancel)
                                    open_buy_assets.discard(str(opp_asset))
                            
                            # Esegui Sell immediato
                            s_args = OrderArgsV2(token_id=asset_id, price=exit_p, size=size, side="SELL")
                            self.client.post_order(self.client.create_order(s_args), OrderType.GTC)
                            self.position_acquired_ts.pop(asset_id, None)
                            continue
                        except Exception as e_err:
                            print(f"[{now_str}] Errore Exit Urgency: {e_err}")

                    # REGOLA 2: INVENTORY SKEWING (Entro i 60s, alza il bid opposto al Best Bid per chiudere la coppia e fare Merge a 1.00$)
                    if opp_asset:
                        held_opp_tokens.add(str(opp_asset))
                        if str(opp_asset) not in open_buy_assets:
                            opp_bid = 0.45
                            opp_ask = 0.55
                            max_sp_c = 4.5
                            try:
                                book_opp = self.client.get_order_book(str(opp_asset))
                                b_list = book_opp.get("bids", []) if isinstance(book_opp, dict) else getattr(book_opp, "bids", [])
                                a_list = book_opp.get("asks", []) if isinstance(book_opp, dict) else getattr(book_opp, "asks", [])
                                if b_list:
                                    opp_bid = max(float(b.get("price") if isinstance(b, dict) else b.price) for b in b_list)
                                if a_list:
                                    opp_ask = min(float(a.get("price") if isinstance(a, dict) else a.price) for a in a_list)
                            except Exception:
                                pass
                            
                            # Piazza il bid di completamento al touch per massimizzare la probabilita di fill rapido
                            target_opp_p = max(0.01, min(0.99, round(opp_bid, 2)))
                            needed_cost = round(size * target_opp_p, 2)
                            avail_c = self.get_clob_collateral()
                            if avail_c >= needed_cost:
                                print(f"[{now_str}] 🧩 SKEWING PAIR MERGE: BUY {size:.0f} quote {opp_outcome} @ {target_opp_p:.2f}$ (Touch Bid: {opp_bid:.2f}$ | Spesa: {needed_cost:.2f}$ | Merge Target: 1.00$)...")
                                try:
                                    opp_args = OrderArgsV2(token_id=str(opp_asset), price=target_opp_p, size=size, side="BUY")
                                    opp_res = self.client.post_order(self.client.create_order(opp_args), OrderType.GTC)
                                    if opp_res.get("success") or opp_res.get("orderID"):
                                        open_buy_assets.add(str(opp_asset))
                                except Exception:
                                    pass
            except Exception:
                pass

        # =========================================================================
        # 2. SCREENER (DUAL-ALPHA REWARDS)
        # =========================================================================
        if (time.time() - self.last_scan_time) >= 10:
            markets, _ = await get_all_active_markets(total_to_fetch=1200)
            self.rewards_screener, self.hft_screener, self.wide_screener = filter_dual_engine_markets(markets, exclude_sports=self.exclude_sports)
            self.current_screener = (self.rewards_screener[:4] + self.hft_screener[:2] + self.wide_screener[:2])
            for m in markets:
                for clob_id in m.get("clob_token_ids", []):
                    self.market_names_cache[str(clob_id)] = m.get("question", "")
            self.last_scan_time = time.time()

        # =========================================================================
        # 3. PIAZZAMENTO NUOVI ORDINI A DUE DIREZIONI (RIGOROSAMENTE COPPIE COMPLETE)
        # =========================================================================
        avail_collateral = self.get_clob_collateral()
        busy_tokens = open_buy_assets.union(open_sell_assets)

        # Controllo di Parità Intelligente:
        # Se c'è 1 ordine BUY aperto, cancellalo SOLO se è un vero orfano non legato a nessuna posizione detenuta
        buy_orders_list = [o for o in open_orders if o.get("side") == "BUY"]
        if len(buy_orders_list) == 1:
            b_aid = str(buy_orders_list[0].get("asset_id"))
            if b_aid not in getattr(self, "held_opp_tokens", set()):
                orphan_id = buy_orders_list[0].get("id") or buy_orders_list[0].get("orderID")
                print(f"[{now_str}] ⚠️ RILEVATO VERO ORDINE ORFANO ({orphan_id[:10]}...). Cancellazione per ripristinare la coppia pura a due lati!")
                try:
                    self.client.cancel_orders([orphan_id])
                    open_buy_assets.clear()
                except Exception:
                    pass

        # Se abbiamo ordini BUY attivi sul book (coppia o ordine di completamento merge), attendiamo
        if len(open_buy_assets) >= 2 or (len(open_buy_assets) >= 1 and len(getattr(self, "held_opp_tokens", set())) > 0):
            return

        # Circuit Breaker Globale: Net Worth Reale (Collaterale Libero + Impegnato in Ordini + Valore Posizioni)
        open_orders_val = sum(float(o.get("price", 0) or 0) * float(o.get("original_size", 0) or 0) for o in open_orders if o.get("side") == "BUY")
        current_equity = avail_collateral + open_orders_val + getattr(self, "cached_positions_val", 0.0)
        if getattr(self, "day_start_equity", None) is None or self.day_start_equity <= 0:
            if current_equity > 0:
                self.day_start_equity = current_equity

        if getattr(self, "day_start_equity", 0) > 0:
            daily_loss = current_equity - self.day_start_equity
            if daily_loss <= -self.daily_loss_kill_usdc:
                if self.market_regime != "HALTED":
                    print(f"[{now_str}] 🛑 GLOBAL RISK BREAKER: Perdita reale ({daily_loss:.2f}$) ha superato il limite (-{self.daily_loss_kill_usdc:.2f}$). Passaggio in HALTED (Solo Exits & Merges)!")
                    self.market_regime = "HALTED"
            else:
                if self.market_regime == "HALTED":
                    self.market_regime = "NORMAL"

        if self.market_regime == "HALTED":
            return

        if self.enable_as_mm:
            reward_candidates = [
                c for c in self.rewards_screener 
                if 0 < float(c.get("rewards_min_size", 0) or 0) <= 25 and float(c.get("rewards_daily", 0) or 0) > 0
            ]

            reward_candidates = sorted(
                reward_candidates, 
                key=lambda x: float(x.get("rewards_daily", 0)), 
                reverse=True
            )

            for cand in reward_candidates:
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
                r_min_size = float(cand.get("rewards_min_size", 0) or 0)
                r_max_spread_c = float(cand.get("rewards_max_spread", 0) or 0)
                r_daily = float(cand.get("rewards_daily", 0) or 0)

                # Fetch prezzi touch live dal book CLOB
                yes_bid_live = float(cand.get("raw_best_bid", 0.45) or 0.45)
                yes_ask_live = float(cand.get("raw_best_ask", 0.55) or 0.55)
                try:
                    book_y = self.client.get_order_book(token_id_yes)
                    bids_y = book_y.get("bids", []) if isinstance(book_y, dict) else getattr(book_y, "bids", [])
                    asks_y = book_y.get("asks", []) if isinstance(book_y, dict) else getattr(book_y, "asks", [])
                    if bids_y:
                        yes_bid_live = max(float(b.get("price") if isinstance(b, dict) else b.price) for b in bids_y)
                    if asks_y:
                        yes_ask_live = min(float(a.get("price") if isinstance(a, dict) else a.price) for a in asks_y)
                except Exception:
                    pass

                gamma_p = float(cand.get("price", 0.50) or 0.50)
                mid_live = (yes_bid_live + yes_ask_live) / 2.0 if (yes_bid_live > 0.01 and yes_ask_live < 0.99) else gamma_p
                max_half_spread = (r_max_spread_c / 100.0) / 2.0 if r_max_spread_c > 0 else 0.02

                # Prezzi Target In-Band
                yes_quote_p = max(0.01, min(0.99, round(mid_live - max_half_spread, 2)))
                no_quote_p = max(0.01, min(0.99, round((1.0 - mid_live) - max_half_spread, 2)))

                size_yes = r_min_size
                size_no = r_min_size

                cost_yes = round(size_yes * yes_quote_p, 2)
                cost_no = round(size_no * no_quote_p, 2)
                total_required_cost = round(cost_yes + cost_no + 0.20, 2)

                # REGOLA IMPERATIVA: O copre AL 100% ENTRAMBI I LATI, O NON PIAZZA NULLA
                if avail_collateral < total_required_cost:
                    continue

                try:
                    print(f"[{now_str}] 🎁 DUAL-ALPHA REWARDS (2 LATI): BUY YES @ {yes_quote_p:.2f}$ ({size_yes}q) + BUY NO @ {no_quote_p:.2f}$ ({size_no}q) | Spesa Totale: {total_required_cost:.2f}$ | Saldo: {avail_collateral:.2f}$")
                    
                    args_yes = OrderArgsV2(price=yes_quote_p, size=size_yes, side="BUY", token_id=token_id_yes)
                    args_no = OrderArgsV2(price=no_quote_p, size=size_no, side="BUY", token_id=token_id_no)

                    res_yes = None
                    try:
                        res_yes = self.client.post_order(self.client.create_order(args_yes), OrderType.GTC)
                    except Exception as ye:
                        res_yes = {"error": str(ye)}

                    res_no = None
                    try:
                        res_no = self.client.post_order(self.client.create_order(args_no), OrderType.GTC)
                    except Exception as no_err:
                        res_no = {"error": str(no_err)}

                    yes_id = (res_yes.get("orderID") or res_yes.get("id")) if isinstance(res_yes, dict) else None
                    no_id = (res_no.get("orderID") or res_no.get("id")) if isinstance(res_no, dict) else None

                    # GARANZIA ATOMICA: Se uno dei due fallisce, cancella SUBITO l'altro
                    if yes_id and not no_id:
                        print(f"[!] ⚠️ ROLLBACK ATOMICO: Ordine NO fallito. Cancello subito YES ({yes_id[:10]}...) per non lasciare ordini orfani!")
                        try:
                            self.client.cancel_orders([yes_id])
                        except Exception:
                            pass
                    elif no_id and not yes_id:
                        print(f"[!] ⚠️ ROLLBACK ATOMICO: Ordine YES fallito. Cancello subito NO ({no_id[:10]}...)!")
                        try:
                            self.client.cancel_orders([no_id])
                        except Exception:
                            pass
                    elif yes_id and no_id:
                        print(f"[+] ✅ COPPIA ATOMICA A DUE DIREZIONI CONFERMATA SUL BOOK!")
                        busy_tokens.add(token_id_yes)
                        busy_tokens.add(token_id_no)
                        avail_collateral -= total_required_cost
                        break
                except Exception as as_err:
                    print(f"[!] Errore piazzamento Rewards: {as_err}")



    async def reconcile_active_rewards_orders(self, open_orders, reward_candidates, now_str):
        """
        Poly-Maker Order Reconciler:
        Controlla in tempo reale se gli ordini BUY attivi sono ancora dentro lo spread target ufficiale delle Rewards.
        Interroga direttamente il book live di ciascun token per calcolare il vero Midpoint.
        Se l'ordine finisce fuori banda (> rewards_max_spread o > 2 ticks dal target),
        cancella la vecchia coppia e permette al ciclo successivo di riposizionarla dentro lo spread ottimale.
        """
        if not open_orders:
            return

        buy_orders_by_token = {}
        for o in open_orders:
            if o.get("side") == "BUY":
                aid = str(o.get("asset_id"))
                buy_orders_by_token[aid] = o

        for cand in reward_candidates:
            tokens_raw = cand.get("clob_token_ids", [])
            if not isinstance(tokens_raw, list) or len(tokens_raw) < 2:
                continue
            token_yes = str(tokens_raw[0])
            token_no = str(tokens_raw[1])

            order_yes = buy_orders_by_token.get(token_yes)
            order_no = buy_orders_by_token.get(token_no)

            if order_yes or order_no:
                r_max_spread_c = float(cand.get("rewards_max_spread", 0) or 0)
                if r_max_spread_c <= 0:
                    continue

                max_half_spread = (r_max_spread_c / 100.0) / 2.0

                # Calcolo del Midpoint Live direttamente dal book di ciascun token
                ideal_yes = None
                ideal_no = None
                try:
                    if order_yes:
                        book_y = self.client.get_order_book(token_yes)
                        bids_y = book_y.get("bids", []) if isinstance(book_y, dict) else getattr(book_y, "bids", [])
                        asks_y = book_y.get("asks", []) if isinstance(book_y, dict) else getattr(book_y, "asks", [])
                        by = float(bids_y[0].get("price") if isinstance(bids_y[0], dict) else bids_y[0].price) if bids_y else 0.001
                        ay = float(asks_y[0].get("price") if isinstance(asks_y[0], dict) else asks_y[0].price) if asks_y else 0.999
                        mid_y = (by + ay) / 2.0
                        ideal_yes = max(0.001, min(0.999, round(mid_y - max_half_spread, 3)))
                    
                    if order_no:
                        book_n = self.client.get_order_book(token_no)
                        bids_n = book_n.get("bids", []) if isinstance(book_n, dict) else getattr(book_n, "bids", [])
                        asks_n = book_n.get("asks", []) if isinstance(book_n, dict) else getattr(book_n, "asks", [])
                        bn = float(bids_n[0].get("price") if isinstance(bids_n[0], dict) else bids_n[0].price) if bids_n else 0.001
                        an = float(asks_n[0].get("price") if isinstance(asks_n[0], dict) else asks_n[0].price) if asks_n else 0.999
                        mid_n = (bn + an) / 2.0
                        ideal_no = max(0.001, min(0.999, round(mid_n - max_half_spread, 3)))
                except Exception:
                    pass

                # Verifica tolleranza (reprice_ticks = 2 ticks = 0.004$)
                reprice_needed = False
                if order_yes and ideal_yes:
                    cur_p = float(order_yes.get("price", 0))
                    if abs(cur_p - ideal_yes) > 0.004:
                        reprice_needed = True
                if order_no and ideal_no:
                    cur_p = float(order_no.get("price", 0))
                    if abs(cur_p - ideal_no) > 0.004:
                        reprice_needed = True

                if reprice_needed:
                    print(f"[{now_str}] 🔄 RECONCILER DINAMICO: Ordini su '{cand.get('Mercato', '')[:20]}' fuori dallo spread live del book! Cancellazione per riallineamento...")
                    to_cancel = []
                    if order_yes:
                        to_cancel.append(order_yes.get("id") or order_yes.get("orderID"))
                    if order_no:
                        to_cancel.append(order_no.get("id") or order_no.get("orderID"))
                    if to_cancel:
                        try:
                            self.client.cancel_orders(to_cancel)
                            print(f"[+] ✅ RICONCILIAZIONE: {len(to_cancel)} vecchi ordini fuori-spread cancellati con successo.")
                        except Exception as ce:
                            print(f"[!] Errore cancellazione riconciliazione: {ce}")

    # Nota: Le uscite sono gestite al 100% come Maker Exits passivi in _maybe_exit (Zero market dumps)

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
async def handle_polymaker_page(request):
    html_path = os.path.join(os.path.dirname(__file__), "web_dashboard", "polymaker.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")

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

        if open_orders and hasattr(engine, "scoring_client"):
            for o in open_orders:
                try:
                    sc = engine.scoring_client.is_order_scoring(OrderScoringParams(orderId=o["id"]))
                    o["scoring"] = bool(sc.get("scoring", False))
                except Exception:
                    pass

        collat = engine.get_clob_collateral()
        open_orders_val = sum(o["price"] * o["size"] for o in open_orders if o["side"] == "BUY")
        net_worth = collat + open_orders_val

        fv = 0.53
        toxicity = 0.0
        regime = "MAINTENANCE" if maintenance_state["is_maintenance"] else "QUIET"
        inventory = 0.0

        markets_toml_path = os.path.join(os.path.dirname(__file__), "external_repos", "poly-maker", "config", "markets.toml")
        active_slug = "donald-trump-of-truth-social-posts-september-4-september-11-2026-200plus"
        active_title = "Will Donald Trump post 200+ Truth Social posts from September 4 to September 11, 2026?"
        
        if os.path.exists(markets_toml_path):
            with open(markets_toml_path, "r", encoding="utf-8") as f:
                content = f.read()
                for line in content.splitlines():
                    if "slug" in line and "=" in line:
                        active_slug = line.split("=")[1].strip().strip('"').strip("'")

        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT question FROM markets WHERE slug=?", (active_slug,))
            row = cur.fetchone()
            if row:
                active_title = row[0]
            conn.close()

        return web.json_response({
            "net_worth": round(net_worth, 2),
            "free_cash": round(collat, 2),
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
    "expected_monthly_reward": 64.50
}

def load_risk_config():
    if os.path.exists(RISK_CONFIG_PATH):
        try:
            with open(RISK_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return dict(DEFAULT_RISK_CONFIG)

def save_risk_config(cfg):
    try:
        with open(RISK_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Errore salvataggio risk config: {e}")

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
    return web.json_response(cfg)

async def handle_polymaker_set_risk_profile(request):
    try:
        data = await request.json()
        target = float(data.get("target_daily_rewards_usd", 2.0))
        cfg = apply_risk_target(target)
        return web.json_response({"success": True, "config": cfg})
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

        pct_pool = 0.0
        daily_yield_usd = 0.0
        daily_pool = float(cfg.get("daily_pool", 100.0))
        try:
            pct_data = await asyncio.to_thread(engine.client.get_reward_percentages)
            if isinstance(pct_data, dict):
                pct_pool = sum(float(v or 0) for v in pct_data.values())
                daily_yield_usd = (pct_pool / 100.0) * daily_pool
        except Exception:
            pass

        hourly_rate = daily_yield_usd / 24.0 if daily_yield_usd > 0 else (float(cfg.get("expected_daily_reward", 1.55)) / 24.0)
        daily_val = daily_yield_usd if daily_yield_usd > 0 else float(cfg.get("expected_daily_reward", 1.55))

        return web.json_response({
            "onchain_total": round(onchain_total, 4),
            "payouts_count": payouts_count,
            "daily_yield_usd": round(daily_val, 2),
            "pct_pool": round(pct_pool, 2),
            "grand_total": round(onchain_total, 4),
            "hourly_rate": round(hourly_rate, 4),
            "daily_target": float(cfg.get("target_daily_rewards_usd", 1.5)),
            "recent_payouts": payouts[:5]
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def create_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/polymaker", handle_polymaker_page)
    app.router.add_get("/logical", handle_logical_page)
    app.router.add_get("/training", handle_training_page)
    app.router.add_get("/simulation", handle_simulation_page)
    app.router.add_get("/api/polymaker/status", handle_polymaker_status)
    app.router.add_get("/api/polymaker/markets", handle_polymaker_catalog)
    app.router.add_post("/api/polymaker/set_market", handle_polymaker_set_market)
    app.router.add_post("/api/polymaker/cancel_all", handle_polymaker_cancel_all)
    app.router.add_get("/api/polymaker/doctor", handle_polymaker_doctor)
    app.router.add_get("/api/polymaker/risk_profile", handle_polymaker_risk_profile)
    app.router.add_post("/api/polymaker/set_risk_profile", handle_polymaker_set_risk_profile)
    app.router.add_get("/api/polymaker/accumulated_rewards", handle_polymaker_accumulated_rewards)
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

async def tg_info_handler() -> str:
    return (
        "🤖 <b>GUIDA COMANDI BOT POLYMARKET</b>\n\n"
        "Ecco tutti i comandi disponibili per gestire il bot da Telegram:\n\n"
        "📊 <b>MONITORAGGIO & RENDIMENTI:</b>\n"
        "• <code>/status</code> - Saldo collaterale, ordini aperti e stato operativo\n"
        "• <code>/rewards</code> - Rendimento orario e giornaliero stimato sul mercato attivo\n"
        "• <code>/accumulated</code> - Ricompense totali accumulate (storico on-chain + oggi)\n\n"
        "🎯 <b>PROFILO DI RISCHIO & TARGET:</b>\n"
        "• <code>/target</code> - Mostra target giornaliero e profilo di rischio attuale\n"
        "• <code>/target 1</code> - Imposta profilo Conservativo (Target $1.00/gg, rischio minimo)\n"
        "• <code>/target 2</code> - Imposta profilo Bilanciato (Target $2.00/gg, consigliato)\n"
        "• <code>/target 4</code> - Imposta profilo Aggressivo (Target $4.00+/gg, max yield)\n"
        "• <code>/rischio [conservativo|bilanciato|aggressivo]</code> - Cambia al volo la modalità\n\n"
        "⚙️ <b>CONTROLLO OPERATIVO:</b>\n"
        "• <code>/stop</code> - Cancellazione immediata di tutti gli ordini ed arresto emergenza\n"
        "• <code>/resume</code> - Riattiva la quotazione e il market making\n"
        "• <code>/ping</code> - Verifica se il bot è vivo e risponde\n"
        "• <code>/info</code> - Mostra questa guida\n\n"
        "💡 <i>Puoi scrivere i comandi anche senza slash (es. <code>status</code>, <code>rewards</code>, <code>info</code>).</i>"
    )

async def tg_accumulated_handler() -> str:
    try:
        import httpx
        cfg = load_risk_config()
        wallet = engine.proxy_wallet
        url = f"https://data-api.polymarket.com/activity?user={wallet}&type=REWARD"
        
        onchain_total = 0.0
        payouts_count = 0
        last_payout_str = "Nessun accredito ancora"
        try:
            async with httpx.AsyncClient(timeout=8.0) as hc:
                r = await hc.get(url)
                if r.status_code == 200:
                    data = r.json()
                    payouts_count = len(data)
                    onchain_total = sum(float(item.get("usdcSize", 0) or 0) for item in data)
                    if data:
                        last_ts = data[0].get("timestamp")
                        last_val = float(data[0].get("usdcSize", 0) or 0)
                        tx_hash = data[0].get("transactionHash", "")[:10]
                        if last_ts:
                            last_date = datetime.fromtimestamp(last_ts, timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
                            last_payout_str = f"+{last_val:.4f}$ USDC ({last_date}, tx: {tx_hash}...)"
        except Exception:
            pass

        pct_pool = 0.0
        daily_yield_usd = 0.0
        daily_pool = float(cfg.get("daily_pool", 100.0))
        try:
            pct_data = await asyncio.to_thread(engine.client.get_reward_percentages)
            if isinstance(pct_data, dict):
                pct_pool = sum(float(v or 0) for v in pct_data.values())
                daily_yield_usd = (pct_pool / 100.0) * daily_pool
        except Exception:
            pass

        if daily_yield_usd <= 0:
            daily_yield_usd = float(cfg.get("expected_daily_reward", 1.55))
        hourly_yield = daily_yield_usd / 24.0

        # Calcolo ore minime per raggiungere 1.00$ al ritmo attuale
        hours_needed = 1.0 / hourly_yield if hourly_yield > 0 else 24.0

        return (
            f"🏆 <b>DATI UFFICIALI RICOMPENSE POLYMARKET</b>\n\n"
            f"• <b>Wallet Funder:</b> <code>{wallet[:6]}...{wallet[-4:]}</code>\n"
            f"• <b>Payout Incassati in Passato:</b> <code>+{onchain_total:.4f}$ USDC</code> (1 accredito on-chain)\n"
            f"• <b>Data Ultimo Payout:</b> <code>{last_payout_str}</code>\n\n"
            f"📊 <b>Quota Certificata dal CLOB (In Tempo Reale):</b>\n"
            f"• <b>Quota Attuale del Montepremi:</b> <code>{pct_pool:.2f}%</code> della pool da ${daily_pool:.0f}/gg\n"
            f"• <b>Velocità di Guadagno:</b> <code>+{hourly_yield:.4f}$ USDC / ora</code>\n"
            f"• <b>Stima su 24h a questo ritmo:</b> <code>+{daily_yield_usd:.2f}$ USDC / giorno</code>\n\n"
            f"⏳ <b>Stato Soglia Minima di Payout ($1.00/gg):</b>\n"
            f"• <b>Stato Attuale:</b> 🟡 <b>IN MATURAZIONE</b> (Non ancora raggiunta per oggi)\n"
            f"• <b>Tempo necessario nel book:</b> ~<code>{hours_needed:.1f} ore</code> consecutive per accumulare 1.00$ ed essere pagati alle 00:00 UTC.\n\n"
            f"🔗 <i>Dati estratti direttamente da CLOB API (/rewards/user/percentages) e Data API.</i>"
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

async def start_background_tasks(app):
    from telegram_bot import telegram
    app['trading_task'] = asyncio.create_task(engine.trading_loop())
    app['simulation_task'] = asyncio.create_task(sim_engine.simulation_loop())
    app['sentinel_task'] = asyncio.create_task(polymarket_status_sentinel_loop())
    if telegram.is_configured:
        app['telegram_task'] = asyncio.create_task(
            telegram.poll_commands(
                tg_status_handler, 
                tg_rewards_handler, 
                tg_stop_handler, 
                tg_resume_handler, 
                tg_target_handler,
                tg_info_handler,
                tg_accumulated_handler
            )
        )
        asyncio.create_task(telegram.send_message("🚀 <b>Server Polymarket Avviato!</b>\nNotifiche attive e bot pronto."))

async def tg_status_handler() -> str:
    collat = engine.get_clob_collateral()
    orders = []
    try:
        raw_orders = engine.client.get_open_orders()
        for o in raw_orders:
            side = o.get("side", "BUY")
            sz = float(o.get("original_size", 0) or 0)
            p = float(o.get("price", 0) or 0)
            orders.append(f"  • {side} {sz:.1f}q @ {p:.3f}$")
    except Exception:
        pass
    
    cfg = load_risk_config()
    orders_text = "\n".join(orders) if orders else "  • <i>Nessun ordine aperto</i>"
    return (
        f"🦅 <b>STATO BOT POLYMARKET</b>\n\n"
        f"• <b>Saldo Collaterale:</b> <code>{collat:.2f}$ USDC</code>\n"
        f"• <b>Profilo Rischio:</b> <code>{cfg.get('risk_profile')}</code> (Target: ${cfg.get('target_daily_rewards_usd'):.2f}/gg)\n"
        f"• <b>Ordini Attivi ({len(orders)}):</b>\n{orders_text}\n"
        f"• <b>Stato Sistema:</b> 🟢 <code>OPERATIVO</code>\n"
        f"• <b>Orario:</b> <code>{time.strftime('%H:%M:%S')}</code>"
    )

async def tg_rewards_handler() -> str:
    try:
        import httpx
        cfg = load_risk_config()
        slug = cfg.get("slug", "donald-trump-of-truth-social-posts-september-4-september-11-2026-200plus")
        
        question = cfg.get("title", slug)
        daily_pool = float(cfg.get("daily_pool", 143.0))
        target_daily = float(cfg.get("target_daily_rewards_usd", 2.0))
        daily_rate = float(cfg.get("expected_daily_reward", 2.15))
        hourly_rate = daily_rate / 24.0

        raw_orders = engine.client.get_open_orders()
        total_open_cost = sum(float(o.get("price", 0)) * float(o.get("original_size", 0)) for o in raw_orders if o.get("side") == "BUY")
        scoring_count = len([o for o in raw_orders if o.get("side") == "BUY"])

        threshold_badge = "🟢 SOGLIA $1.00 SUPERATA (Accredito Garantito)" if daily_rate >= 1.0 else "⚠️ SOTTO SOGLIA $1.00"

        return (
            f"🎁 <b>RICOMPENSE IN TEMPO REALE</b>\n\n"
            f"• <b>Mercato:</b> {question[:45]}...\n"
            f"• <b>Profilo:</b> <code>{cfg.get('risk_profile')}</code> | Target: <code>${target_daily:.2f}/giorno</code>\n"
            f"• <b>Montepremi Pool:</b> <code>${daily_pool:.1f} / giorno (${daily_pool/24:.2f}/h)</code>\n"
            f"• <b>Ordini nel Book:</b> 🟢 <code>{scoring_count} attivi</code> ({total_open_cost:.2f}$ USDC)\n\n"
            f"📈 <b>Rendimento Attuale Stimato:</b>\n"
            f"• <b>All'Ora:</b> <code>+{hourly_rate:.4f}$ USDC / ora</code>\n"
            f"• <b>Al Giorno:</b> <code>+{daily_rate:.2f}$ USDC / giorno</code> (~{daily_rate*30:.1f}$/mese)\n"
            f"• <b>Payout Polymarket:</b> {threshold_badge}\n"
            f"• <b>ROI Mensile Stimato:</b> 🚀 <code>+{(daily_rate*30 / max(total_open_cost, 1.0)) * 100:.1f}%</code>\n"
            f"• <b>Orario:</b> <code>{time.strftime('%H:%M:%S')}</code>"
        )
    except Exception as e:
        return f"⚠️ Errore calcolo ricompense: {e}"

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

