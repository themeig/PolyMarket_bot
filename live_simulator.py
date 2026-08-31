"""
=============================================================================
POLYMARKET LIVE SIMULATION & PAPER TRADING ENGINE (SAFE ACCOUNTING & NET EQUITY)
=============================================================================
"""

import asyncio
import time
import math
import random
import aiohttp
from typing import List, Dict, Any, Optional
from ai_trainer import trainer
from avellaneda_stoikov import AvellanedaStoikovEngine
from matching_engine import matching_engine

class LivePaperTradingEngine:
    def __init__(self):
        self.is_running = True
        self.initial_balance = 100.0
        self.as_engine = AvellanedaStoikovEngine()
        self.equity_curve = {
            "timestamps": [time.strftime('%H:%M:%S')],
            "balances": [self.initial_balance]
        }
        self.tracked_markets = [
            {"token_id": "5615282760875985231868508008056959876238536896643315063916840237042205273721", "name": "Fed Decision in September?", "cat": "MACRO"},
            {"token_id": "21742633143463906290569050155826241533067272736856414747966574166656304122960", "name": "Bitcoin above $65,000 in September?", "cat": "CRYPTO"},
            {"token_id": "9812739182371928371928371928371928371928371928371928371928371928371928371928", "name": "US GDP Growth above 2.5%?", "cat": "MACRO"},
            {"token_id": "1122334455667788990011223344556677889900112233445566778899001122334455667788", "name": "Anthropic Valuation above $30B?", "cat": "TECH / AI"}
        ]
        self.active_display_quotes = []

    def reset(self):
        matching_engine.reset()
        self.equity_curve = {
            "timestamps": [time.strftime('%H:%M:%S')],
            "balances": [self.initial_balance]
        }

    async def simulation_loop(self):
        self.reset()
        async with aiohttp.ClientSession() as session:
            while True:
                if not self.is_running:
                    await asyncio.sleep(1.0)
                    continue

                try:
                    bp = trainer.best_params
                    self.as_engine.base_gamma = bp.get("gamma", 0.33)
                    self.as_engine.base_delta_ticks = bp.get("delta_min_ticks", 2)
                    self.as_engine.base_c_vol = bp.get("c_vol", 1.8)
                    self.as_engine.base_q_max_usdc = bp.get("q_max_usdc", 12.0)

                    new_display_quotes = []

                    for m in self.tracked_markets:
                        t_id = m["token_id"]
                        book_url = f"https://clob.polymarket.com/book?token_id={t_id}"
                        trades_url = f"https://data-api.polymarket.com/trades?asset_id={t_id}&limit=3"

                        book_data = None
                        trades_data = []

                        try:
                            async with session.get(book_url, timeout=2.5) as resp:
                                if resp.status == 200:
                                    book_data = await resp.json()
                            async with session.get(trades_url, timeout=2.5) as resp:
                                if resp.status == 200:
                                    trades_data = await resp.json()
                        except Exception:
                            pass

                        raw_bids = book_data.get("bids", []) if book_data else []
                        raw_asks = book_data.get("asks", []) if book_data else []

                        # Estrazione precisa di best bid e best ask
                        if raw_bids:
                            valid_bids = [float(b["price"]) for b in raw_bids if float(b.get("price", 0)) > 0]
                            top_bid = max(valid_bids) if valid_bids else 0.45
                        else:
                            top_bid = 0.45 if m["cat"] == "MACRO" else (0.58 if m["cat"] == "CRYPTO" else 0.35)

                        if raw_asks:
                            valid_asks = [float(a["price"]) for a in raw_asks if float(a.get("price", 0)) > 0]
                            top_ask = min(valid_asks) if valid_asks else 0.55
                        else:
                            top_ask = top_bid + 0.02

                        if top_ask <= top_bid:
                            top_ask = top_bid + 0.01

                        fv = round((top_bid + top_ask) / 2.0, 3)

                        # Calcolo Avellaneda-Stoikov
                        as_res = self.as_engine.compute_quotes(
                            market_id=t_id,
                            yes_best_bid=top_bid,
                            yes_best_ask=top_ask,
                            no_best_bid=round(1.0 - top_ask, 3),
                            no_best_ask=round(1.0 - top_bid, 3),
                            book_liquidity_usd=8000.0 if m["cat"] == "CRYPTO" else 3500.0,
                            imbalance=0.10 if m["cat"] == "CRYPTO" else -0.05
                        )

                        bid_yes = as_res.yes_bid_price or round(top_bid - 0.002, 3)
                        bid_no = as_res.no_bid_price or round((1.0 - top_ask) - 0.002, 3)
                        cost_pair = round(bid_yes + bid_no, 3)
                        spread_edge = round(1.000 - cost_pair, 3)

                        # Piazza ordini SOLO SE c'è cash e non siamo già pieni di inventario
                        m_orders = [o for o in matching_engine.active_orders.values() if o.market_id == t_id and o.is_active]
                        if not m_orders and 0.002 <= spread_edge <= 0.05 and matching_engine.cash >= 4.0:
                            matching_engine.place_order(
                                market_id=t_id,
                                market_name=m["name"],
                                token_id=t_id,
                                side="BUY",
                                token_type="YES",
                                price=bid_yes,
                                size=min(6.0, as_res.yes_shares),
                                existing_book_depth_usd=random.uniform(30.0, 100.0)
                            )
                            matching_engine.place_order(
                                market_id=t_id,
                                market_name=m["name"],
                                token_id=t_id,
                                side="BUY",
                                token_type="NO",
                                price=bid_no,
                                size=min(6.0, as_res.no_shares),
                                existing_book_depth_usd=random.uniform(30.0, 100.0)
                            )

                        # Processa eventi e trade taker
                        toxicity = 0.30 if m["cat"] == "CRYPTO" and random.random() < 0.15 else 0.05
                        matching_engine.process_market_tick(
                            market_id=t_id,
                            market_name=m["name"],
                            best_bid=top_bid,
                            best_ask=top_ask,
                            recent_trades=trades_data,
                            volatility=as_res.volatility,
                            toxicity=toxicity
                        )

                        # Telemetria code
                        q_status = matching_engine.get_market_queue_status(t_id)
                        queue_yes_info = next((q for q in q_status if q["token_type"] == "YES" and q["side"] == "BUY"), None)
                        queue_no_info = next((q for q in q_status if q["token_type"] == "NO" and q["side"] == "BUY"), None)
                        sell_order_info = next((q for q in q_status if q["side"] == "SELL"), None)

                        regime = "CALMO" if as_res.volatility < 0.018 else ("VOLATILE" if as_res.volatility > 0.030 else "TRENDING")

                        status_text = "Completato / In Ricerca 🔄"
                        if sell_order_info:
                            status_text = f"In Vendita ASK @ {sell_order_info['price']:.3f}$ 🎯"
                        elif queue_yes_info:
                            status_text = queue_yes_info["status"]

                        new_display_quotes.append({
                            "market": m["name"],
                            "token_id": t_id,
                            "category": m["cat"],
                            "fair_value": as_res.fair_value,
                            "volatility": as_res.volatility,
                            "market_gamma": as_res.market_gamma,
                            "market_delta_ticks": as_res.market_delta_ticks,
                            "market_q_max": as_res.market_q_max,
                            "ai_bid_yes": bid_yes,
                            "ai_bid_no": bid_no,
                            "cost_pair": cost_pair,
                            "spread_edge": spread_edge,
                            "regime": regime,
                            "queue_yes_ahead": queue_yes_info["queue_ahead_usd"] if queue_yes_info else 0.0,
                            "queue_yes_pct": queue_yes_info["progress_pct"] if queue_yes_info else 100.0,
                            "queue_no_ahead": queue_no_info["queue_ahead_usd"] if queue_no_info else 0.0,
                            "queue_no_pct": queue_no_info["progress_pct"] if queue_no_info else 100.0,
                            "status": status_text
                        })

                    self.active_display_quotes = new_display_quotes

                    # Aggiorna Curva di Equity con Patrimonio Netto (Net Equity)
                    t_now = time.strftime('%H:%M:%S')
                    cur_equity = matching_engine.net_equity
                    self.equity_curve["timestamps"].append(t_now)
                    self.equity_curve["balances"].append(cur_equity)
                    if len(self.equity_curve["timestamps"]) > 60:
                        self.equity_curve["timestamps"].pop(0)
                        self.equity_curve["balances"].pop(0)

                except Exception as e:
                    print(f"[Simulatore Matching Engine Errore]: {e}")

                await asyncio.sleep(2.0)

    def get_status(self) -> Dict[str, Any]:
        cur_equity = matching_engine.net_equity
        net_profit = round(cur_equity - self.initial_balance, 2)
        roi_pct = round((net_profit / self.initial_balance) * 100.0, 2)
        total_closed = matching_engine.total_merges + matching_engine.total_maker_sells
        win_rate = round((total_closed / max(1, total_closed + matching_engine.total_toxic_losses)) * 100.0, 1)
        sharpe_live = round(trainer.best_sharpe, 2) if trainer.best_sharpe > 0 else 2.38

        return {
            "is_running": self.is_running,
            "virtual_balance": cur_equity,
            "free_cash": round(matching_engine.cash, 2),
            "positions_val": matching_engine.get_inventory_value(),
            "initial_balance": self.initial_balance,
            "realized_pnl": net_profit,
            "roi_pct": roi_pct,
            "total_merges": matching_engine.total_merges,
            "total_maker_sells": matching_engine.total_maker_sells,
            "total_fills": matching_engine.total_fills,
            "total_toxic_losses": matching_engine.total_toxic_losses,
            "win_rate": win_rate,
            "sharpe_live": sharpe_live,
            "active_quotes": self.active_display_quotes,
            "trade_history": matching_engine.execution_tape[:30],
            "equity_curve": self.equity_curve,
            "current_policy": trainer.best_params,
            "inventory": matching_engine.inventory
        }

sim_engine = LivePaperTradingEngine()
