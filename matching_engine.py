"""
=============================================================================
POLYMARKET L2 FIFO MATCHING ENGINE (STRICT COLLATERAL & MAKER EXITS)
=============================================================================
- Strictly enforces Free Cash Collateral (Cash CAN NEVER GO NEGATIVE).
- Enforces Inventory Caps (q_max): Stops buying when full on a side.
- Implements Maker Exits (Auto SELL Limits): Recovers cash by selling excess inventory.
- Calculates Total Net Equity: Cash + Market Value of Held Inventory.
=============================================================================
"""

import time
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

@dataclass
class SimulatedOrder:
    order_id: str
    market_id: str
    market_name: str
    token_id: str
    side: str  # "BUY" or "SELL"
    token_type: str  # "YES" or "NO"
    price: float
    size: float
    filled_size: float = 0.0
    queue_ahead_usd: float = 0.0
    initial_queue_usd: float = 0.0
    created_at: float = field(default_factory=time.time)
    status: str = "IN_CODA"  # "IN_CODA", "AVANZAMENTO", "PARZIALE", "ESEGUITO", "CANCELLATO"

    @property
    def queue_progress_pct(self) -> float:
        if self.initial_queue_usd <= 0:
            return 100.0 if self.filled_size >= self.size else 50.0
        consumed = max(0.0, self.initial_queue_usd - self.queue_ahead_usd)
        return min(100.0, round((consumed / self.initial_queue_usd) * 100.0, 1))

    @property
    def is_active(self) -> bool:
        return self.status in ("IN_CODA", "AVANZAMENTO", "PARZIALE")


class L2MatchingEngine:
    def __init__(self, initial_cash: float = 100.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.realized_pnl = 0.0
        self.active_orders: Dict[str, SimulatedOrder] = {}
        self.inventory: Dict[str, Dict[str, Any]] = {}
        self.execution_tape: List[Dict[str, Any]] = []
        self.total_merges = 0
        self.total_fills = 0
        self.total_maker_sells = 0
        self.total_toxic_losses = 0

    def reset(self):
        self.cash = self.initial_cash
        self.realized_pnl = 0.0
        self.active_orders.clear()
        self.inventory.clear()
        self.execution_tape.clear()
        self.total_merges = 0
        self.total_fills = 0
        self.total_maker_sells = 0
        self.total_toxic_losses = 0

    def get_inventory_value(self) -> float:
        tot = 0.0
        for inv in self.inventory.values():
            tot += (inv.get("YES", 0.0) * inv.get("cur_p_yes", 0.50))
            tot += (inv.get("NO", 0.0) * inv.get("cur_p_no", 0.50))
        return round(tot, 2)

    @property
    def net_equity(self) -> float:
        return round(self.cash + self.get_inventory_value(), 2)

    def place_order(
        self,
        market_id: str,
        market_name: str,
        token_id: str,
        side: str,
        token_type: str,
        price: float,
        size: float,
        existing_book_depth_usd: float
    ) -> Optional[SimulatedOrder]:
        cost = round(size * price, 2)
        
        # 1. CONTROLLO COLLATERALE RIGIDO: Non puoi comprare se non hai abbastanza cash USDC!
        if side == "BUY" and self.cash < cost:
            return None

        # 2. CONTROLLO CAP INVENTARIO (Q_MAX): Non accumulare più di 12 quote per lato su un singolo mercato
        cur_inv = self.inventory.get(market_id, {}).get(token_type, 0.0)
        if side == "BUY" and cur_inv >= 12.0:
            return None

        order_id = f"sim_{side.lower()}_{token_type.lower()}_{int(time.time()*1000)}_{random.randint(100,999)}"
        queue_ahead = max(5.0, existing_book_depth_usd * random.uniform(0.3, 0.8))
        
        order = SimulatedOrder(
            order_id=order_id,
            market_id=market_id,
            market_name=market_name,
            token_id=token_id,
            side=side,
            token_type=token_type,
            price=price,
            size=size,
            queue_ahead_usd=queue_ahead,
            initial_queue_usd=queue_ahead
        )
        self.active_orders[order_id] = order
        return order

    def cancel_order(self, order_id: str, reason: str = "REPRICE"):
        if order_id in self.active_orders:
            del self.active_orders[order_id]

    def process_market_tick(
        self,
        market_id: str,
        market_name: str,
        best_bid: float,
        best_ask: float,
        recent_trades: List[Dict[str, Any]],
        volatility: float,
        toxicity: float
    ):
        now_str = time.strftime('%H:%M:%S')

        # Aggiorna prezzi correnti nell'inventario per il calcolo dell'Equity
        if market_id not in self.inventory:
            self.inventory[market_id] = {
                "YES": 0.0, "NO": 0.0,
                "cost_yes": 0.0, "cost_no": 0.0,
                "cur_p_yes": best_bid, "cur_p_no": round(1.0 - best_ask, 3),
                "market_name": market_name
            }
        else:
            self.inventory[market_id]["cur_p_yes"] = best_bid
            self.inventory[market_id]["cur_p_no"] = round(1.0 - best_ask, 3)

        orders_for_market = [o for o in self.active_orders.values() if o.market_id == market_id and o.is_active]

        for order in orders_for_market:
            trade_volume_usd = random.uniform(5.0, 40.0) if len(recent_trades) > 0 else random.uniform(1.0, 10.0)

            # A. GESTIONE ORDINI DI ACQUISTO (BUY)
            if order.side == "BUY" and order.price >= (best_bid - 0.003):
                if order.queue_ahead_usd > trade_volume_usd:
                    order.queue_ahead_usd -= trade_volume_usd
                    order.status = "AVANZAMENTO"
                else:
                    # FILL ACQUISTO CONFERMATO
                    fill_amount = order.size - order.filled_size
                    cost = round(fill_amount * order.price, 2)

                    # Verifica che il cash sia sufficiente
                    if self.cash >= cost:
                        self.cash -= cost
                        order.filled_size = order.size
                        order.queue_ahead_usd = 0.0
                        order.status = "ESEGUITO"
                        self.total_fills += 1

                        if order.token_type == "YES":
                            self.inventory[market_id]["YES"] += fill_amount
                            self.inventory[market_id]["cost_yes"] += cost
                        else:
                            self.inventory[market_id]["NO"] += fill_amount
                            self.inventory[market_id]["cost_no"] += cost

                        self.execution_tape.insert(0, {
                            "timestamp": now_str,
                            "market": market_name,
                            "event": f"⚡ FILL {order.token_type}",
                            "details": f"{fill_amount:.1f} quote @ {order.price:.3f}$ (Spesa: {cost:.2f}$)",
                            "pnl": f"-{cost:.2f}$ (Acquisto)",
                            "type": "FILL"
                        })

                    if order.order_id in self.active_orders:
                        del self.active_orders[order.order_id]

            # B. GESTIONE ORDINI DI VENDITA LIMITE (MAKER EXITS / TAKE-PROFIT)
            elif order.side == "SELL" and order.price <= (best_ask + 0.003):
                if order.queue_ahead_usd > trade_volume_usd:
                    order.queue_ahead_usd -= trade_volume_usd
                    order.status = "AVANZAMENTO"
                else:
                    # FILL VENDITA CONFERMATO: RECUPERO CASH + PROFITTO SPREAD!
                    fill_amount = order.size
                    revenue = round(fill_amount * order.price, 2)
                    inv = self.inventory[market_id]

                    avg_cost_unit = (inv["cost_yes"] / inv["YES"]) if order.token_type == "YES" and inv["YES"] > 0 else (inv["cost_no"] / max(1, inv["NO"]))
                    cost_basis = round(fill_amount * avg_cost_unit, 2)
                    profit = round(revenue - cost_basis, 2)

                    self.cash += revenue
                    self.realized_pnl += profit
                    self.total_maker_sells += 1

                    if order.token_type == "YES":
                        inv["YES"] = max(0.0, inv["YES"] - fill_amount)
                        inv["cost_yes"] = max(0.0, inv["cost_yes"] - cost_basis)
                    else:
                        inv["NO"] = max(0.0, inv["NO"] - fill_amount)
                        inv["cost_no"] = max(0.0, inv["cost_no"] - cost_basis)

                    self.execution_tape.insert(0, {
                        "timestamp": now_str,
                        "market": market_name,
                        "event": f"🚀 MAKER SELL {order.token_type} (Take-Profit)",
                        "details": f"Vendute {fill_amount:.1f} quote @ {order.price:.3f}$ -> Incassati {revenue:.2f}$ USDC",
                        "pnl": f"+{profit:.2f} $",
                        "type": "SELL"
                    })

                    if order.order_id in self.active_orders:
                        del self.active_orders[order.order_id]

        # 3. VERIFICA COMPLETE SET MERGE (QUANDO POSSEDIAMO SIA YES CHE NO)
        if market_id in self.inventory:
            inv = self.inventory[market_id]
            mergeable_shares = min(inv["YES"], inv["NO"])

            if mergeable_shares >= 1.0:
                cost_per_yes = (inv["cost_yes"] / inv["YES"]) if inv["YES"] > 0 else 0.45
                cost_per_no = (inv["cost_no"] / inv["NO"]) if inv["NO"] > 0 else 0.52
                total_spent = round(mergeable_shares * (cost_per_yes + cost_per_no), 3)
                payout = round(mergeable_shares * 1.000, 3)
                net_profit = round(payout - total_spent, 3)

                self.cash += payout
                self.realized_pnl += net_profit
                self.total_merges += 1

                inv["YES"] -= mergeable_shares
                inv["NO"] -= mergeable_shares
                inv["cost_yes"] = max(0.0, inv["cost_yes"] - (mergeable_shares * cost_per_yes))
                inv["cost_no"] = max(0.0, inv["cost_no"] - (mergeable_shares * cost_per_no))

                self.execution_tape.insert(0, {
                    "timestamp": now_str,
                    "market": market_name,
                    "event": "💎 COMPLETE SET MERGE ON-CHAIN",
                    "details": f"Fuse {mergeable_shares:.1f} YES + {mergeable_shares:.1f} NO -> Incassati {payout:.2f}$ USDC",
                    "pnl": f"+{net_profit:.3f} $",
                    "type": "MERGE"
                })

        # 4. GESTIONE AUTOMATICA ORDINI DI VENDITA PER INVENTARIO SPROVVISTO DELL'ALTRA GAMBA
        if market_id in self.inventory:
            inv = self.inventory[market_id]
            # Se abbiamo quote YES senza NO e non c'è già un ordine di vendita attivo
            has_sell_yes = any(o for o in self.active_orders.values() if o.market_id == market_id and o.side == "SELL" and o.token_type == "YES")
            if inv["YES"] >= 2.0 and inv["NO"] == 0 and not has_sell_yes:
                avg_p = inv["cost_yes"] / inv["YES"]
                target_ask = round(max(avg_p + 0.02, best_ask), 3)
                self.place_order(
                    market_id=market_id,
                    market_name=market_name,
                    token_id=market_id,
                    side="SELL",
                    token_type="YES",
                    price=target_ask,
                    size=inv["YES"],
                    existing_book_depth_usd=random.uniform(20.0, 60.0)
                )

        if len(self.execution_tape) > 50:
            self.execution_tape = self.execution_tape[:50]

    def get_market_queue_status(self, market_id: str) -> List[Dict[str, Any]]:
        orders = [o for o in self.active_orders.values() if o.market_id == market_id and o.is_active]
        res = []
        for o in orders:
            res.append({
                "order_id": o.order_id,
                "side": o.side,
                "token_type": o.token_type,
                "price": o.price,
                "size": o.size,
                "queue_ahead_usd": round(o.queue_ahead_usd, 1),
                "progress_pct": o.queue_progress_pct,
                "status": o.status
            })
        return res

matching_engine = L2MatchingEngine(initial_cash=100.0)
