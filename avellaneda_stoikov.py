"""
=============================================================================
AVELLANEDA-STOIKOV PER-MARKET ADAPTIVE STRATEGY ENGINE
=============================================================================
"""

import math
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List

_EPS = 1e-9


def round_to_tick(price: float, tick: float = 0.001, decimals: int = 3, *, up: bool = False) -> float:
    n = price / tick
    n = math.ceil(n - _EPS) if up else math.floor(n + _EPS)
    p = round(n * tick, decimals)
    return min(max(p, tick), 1.0 - tick)


def clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


class Ewma:
    __slots__ = ("halflife", "_value", "_last_ts", "_initialized")

    def __init__(self, halflife_s: float = 30.0) -> None:
        self.halflife = max(1.0, halflife_s)
        self._value = 0.0
        self._last_ts = 0.0
        self._initialized = False

    def update(self, value: float, ts: Optional[float] = None) -> float:
        if ts is None:
            ts = time.time()
        if not self._initialized:
            self._value = value
            self._last_ts = ts
            self._initialized = True
            return self._value
        dt = max(0.0, ts - self._last_ts)
        decay = 0.5 ** (dt / self.halflife)
        self._value = decay * self._value + (1.0 - decay) * value
        self._last_ts = ts
        return self._value

    @property
    def value(self) -> float:
        return self._value


class VolatilityEstimator:
    def __init__(self, short_halflife_s: float = 20.0, long_halflife_s: float = 120.0):
        self._short = Ewma(short_halflife_s)
        self._long = Ewma(long_halflife_s)
        self._last_fv: Optional[float] = None
        self._last_ts = 0.0

    def update(self, fv: float, ts: Optional[float] = None) -> float:
        if ts is None:
            ts = time.time()
        if self._last_fv is not None:
            ret = fv - self._last_fv
            sq = ret * ret
            self._short.update(sq, ts)
            self._long.update(sq, ts)
        self._last_fv = fv
        self._last_ts = ts
        return self.volatility

    @property
    def volatility(self) -> float:
        return math.sqrt(max(0.0, self._short.value))


@dataclass
class ASQuoteResult:
    market_id: str
    fair_value: float
    reservation_price: float
    half_spread: float
    inventory_u: float
    volatility: float
    market_gamma: float
    market_delta_ticks: int
    market_q_max: float
    yes_bid_price: Optional[float]
    no_bid_price: Optional[float]
    yes_shares: float
    no_shares: float
    expected_edge: float


class AvellanedaStoikovEngine:
    def __init__(
        self,
        base_gamma: float = 0.25,
        base_delta_ticks: int = 2,
        base_c_vol: float = 1.6,
        base_q_max_usdc: float = 12.0,
        base_size_usdc: float = 1.6,
        tick_size: float = 0.001,
        decimals: int = 3,
        **kwargs
    ):
        self.base_gamma = kwargs.get("gamma", base_gamma)
        self.base_delta_ticks = kwargs.get("delta_min_ticks", base_delta_ticks)
        self.base_c_vol = kwargs.get("c_vol", base_c_vol)
        self.base_q_max_usdc = kwargs.get("q_max_usdc", base_q_max_usdc)
        self.base_size_usdc = kwargs.get("base_size_usdc", base_size_usdc)
        self.tick_size = tick_size
        self.decimals = decimals
        self.vol_estimators: Dict[str, VolatilityEstimator] = {}

    @property
    def gamma(self):
        return self.base_gamma

    @gamma.setter
    def gamma(self, val):
        self.base_gamma = val

    @property
    def delta_min_ticks(self):
        return self.base_delta_ticks

    @delta_min_ticks.setter
    def delta_min_ticks(self, val):
        self.base_delta_ticks = val

    @property
    def c_vol(self):
        return self.base_c_vol

    @c_vol.setter
    def c_vol(self, val):
        self.base_c_vol = val

    @property
    def q_max_usdc(self):
        return self.base_q_max_usdc

    @q_max_usdc.setter
    def q_max_usdc(self, val):
        self.base_q_max_usdc = val

    def get_or_create_vol(self, market_id: str) -> VolatilityEstimator:
        if market_id not in self.vol_estimators:
            self.vol_estimators[market_id] = VolatilityEstimator()
        return self.vol_estimators[market_id]

    def compute_quotes(
        self,
        market_id: str,
        yes_best_bid: Optional[float],
        yes_best_ask: Optional[float],
        no_best_bid: Optional[float],
        no_best_ask: Optional[float],
        pos_yes_shares: float = 0.0,
        pos_no_shares: float = 0.0,
        book_liquidity_usd: float = 5000.0,
        imbalance: float = 0.0,
        now: Optional[float] = None
    ) -> ASQuoteResult:
        if now is None:
            now = time.time()

        # 1. Fair Value
        if yes_best_bid and yes_best_ask and yes_best_ask > yes_best_bid:
            fv = (yes_best_bid + yes_best_ask) / 2.0
        elif yes_best_bid:
            fv = yes_best_bid + self.tick_size
        elif yes_best_ask:
            fv = yes_best_ask - self.tick_size
        else:
            fv = 0.50

        fv = clamp(fv, self.tick_size * 2, 1.0 - self.tick_size * 2)

        # 2. Volatilità EWMA SPECIFICA del mercato
        vol_est = self.get_or_create_vol(market_id)
        sigma = vol_est.update(fv, now)
        if sigma <= 0:
            sigma = 0.015

        # =========================================================================
        # 3. CALIBRAZIONE PARAMETRI PERSONALIZZATI PER QUESTO SINGOLO MERCATO
        # =========================================================================
        extremity = min(1.0, abs(fv - 0.50) / 0.50)
        m_gamma = round(clamp(self.base_gamma * (1.0 + 1.5 * extremity + 12.0 * sigma), 0.05, 1.20), 3)

        vol_spread_ticks = int(round(sigma * 80.0))
        liq_penalty_ticks = 1 if book_liquidity_usd < 2000.0 else 0
        m_delta_ticks = int(clamp(self.base_delta_ticks + vol_spread_ticks + liq_penalty_ticks, 1, 8))

        m_q_max = round(clamp(self.base_q_max_usdc * (1.0 - 0.4 * extremity) * min(1.0, book_liquidity_usd / 4000.0), 3.0, 20.0), 1)

        # 4. Calcolo Inventario & Skew
        net_shares = pos_yes_shares - pos_no_shares
        q_max_shares = m_q_max / max(fv, self.tick_size)
        u = clamp(net_shares / q_max_shares, -1.0, 1.0) if q_max_shares > 0 else 0.0

        skew = m_gamma * sigma * (u + imbalance * 0.3)
        r = clamp(fv - skew, self.tick_size, 1.0 - self.tick_size)

        # 5. Half-Spread
        delta = m_delta_ticks * self.tick_size

        # 6. Target Dual-Bid Prices
        raw_yes_bid = r - delta
        raw_no_bid = (1.0 - r) - delta

        yes_bid = self._sanitize_bid(raw_yes_bid, yes_best_bid, yes_best_ask, fv, m_delta_ticks)
        no_bid = self._sanitize_bid(raw_no_bid, no_best_bid, no_best_ask, 1.0 - fv, m_delta_ticks)

        # 7. Sizing
        scale_yes = max(0.2, 1.0 - max(u, 0.0))
        scale_no = max(0.2, 1.0 - max(-u, 0.0))

        yes_shares = max(1.0, round((self.base_size_usdc * scale_yes) / max(yes_bid or fv, self.tick_size), 1))
        no_shares = max(1.0, round((self.base_size_usdc * scale_no) / max(no_bid or (1.0 - fv), self.tick_size), 1))

        expected_edge = round(1.000 - ((yes_bid or 0) + (no_bid or 0)), 3) if (yes_bid and no_bid) else 0.0

        return ASQuoteResult(
            market_id=market_id,
            fair_value=round(fv, 3),
            reservation_price=round(r, 3),
            half_spread=round(delta, 3),
            inventory_u=round(u, 2),
            volatility=round(sigma, 4),
            market_gamma=m_gamma,
            market_delta_ticks=m_delta_ticks,
            market_q_max=m_q_max,
            yes_bid_price=yes_bid,
            no_bid_price=no_bid,
            yes_shares=yes_shares,
            no_shares=no_shares,
            expected_edge=expected_edge
        )

    def _sanitize_bid(
        self,
        target: float,
        best_bid: Optional[float],
        best_ask: Optional[float],
        fv: float,
        delta_ticks: int
    ) -> Optional[float]:
        target = min(target, fv - delta_ticks * self.tick_size)
        if best_bid is not None and target >= best_bid:
            target = best_bid
        if best_ask is not None and target >= best_ask:
            target = best_ask - self.tick_size

        p = round_to_tick(target, self.tick_size, self.decimals, up=False)
        if p <= 0.001 or p >= 0.999:
            return None
        return p
