from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class TradeRecord:
    price: float
    qty: float
    is_buyer_maker: bool
    timestamp_ms: int

    @property
    def delta(self) -> float:
        """Positive = taker buy, Negative = taker sell."""
        return self.qty if not self.is_buyer_maker else -self.qty


class FlowEngine:
    """Computes delta, CVD, aggression ratio, tape speed, and volume stats."""

    def __init__(
        self,
        cvd_window: int = 1000,
        aggression_window: int = 100,
        tape_speed_window_s: float = 10.0,
    ) -> None:
        self._trades: deque[TradeRecord] = deque(maxlen=cvd_window)
        self._cvd: float = 0.0
        self._tape_s = tape_speed_window_s
        self._agg_n = aggression_window

    def on_trade(
        self, price: str, qty: str, is_buyer_maker: bool, timestamp_ms: int
    ) -> None:
        try:
            rec = TradeRecord(float(price), float(qty), is_buyer_maker, timestamp_ms)
        except (ValueError, TypeError):
            return
        self._trades.append(rec)
        self._cvd += rec.delta

    def reset_cvd(self) -> None:
        self._cvd = 0.0

    @property
    def cvd(self) -> float:
        return self._cvd

    def compute_delta(self, window: int = 50) -> float:
        recent = list(self._trades)[-window:]
        return sum(t.delta for t in recent)

    def compute_aggression_ratio(self) -> float | None:
        recent = list(self._trades)[-self._agg_n:]
        if not recent:
            return None
        total = sum(t.qty for t in recent)
        if total == 0.0:
            return None
        buy_vol = sum(t.qty for t in recent if not t.is_buyer_maker)
        return buy_vol / total

    def compute_tape_speed_per_min(self, now_ms: int) -> float:
        if self._tape_s <= 0:
            return 0.0
        cutoff = now_ms - int(self._tape_s * 1000)
        count = sum(1 for t in self._trades if t.timestamp_ms >= cutoff)
        return count / self._tape_s * 60.0

    def compute_buy_sell_volumes(self, window: int = 100) -> tuple[float, float]:
        recent = list(self._trades)[-window:]
        buy_vol = sum(t.qty for t in recent if not t.is_buyer_maker)
        sell_vol = sum(t.qty for t in recent if t.is_buyer_maker)
        return buy_vol, sell_vol

    def compute_large_trade_stats(
        self, price: float, threshold_usd: float, window: int = 100
    ) -> tuple[int, int]:
        if price <= 0 or threshold_usd <= 0:
            return 0, 0
        recent = list(self._trades)[-window:]
        large_buys = sum(
            1 for t in recent if not t.is_buyer_maker and t.qty * price >= threshold_usd
        )
        large_sells = sum(
            1 for t in recent if t.is_buyer_maker and t.qty * price >= threshold_usd
        )
        return large_buys, large_sells

    def cvd_slope(self, window: int = 20) -> float | None:
        """Linear slope of the running CVD over the last `window` trades.

        Positive slope = accelerating buy pressure.
        """
        recent = list(self._trades)[-window:]
        if len(recent) < 3:
            return None
        cumulative: list[float] = []
        running = 0.0
        for t in recent:
            running += t.delta
            cumulative.append(running)
        n = len(cumulative)
        x_mean = (n - 1) / 2.0
        y_mean = sum(cumulative) / n
        num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(cumulative))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return (num / den) if den != 0 else None
