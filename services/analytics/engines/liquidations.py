from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class LiquidationRecord:
    side: str       # "BUY" | "SELL"
    qty: float
    price: float
    timestamp_ms: int


@dataclass
class LiquidationCluster:
    price_mid: float
    bucket_low: float
    bucket_high: float
    count: int
    total_qty: float
    total_usd: float
    long_qty: float    # SELL-side liq = long positions being liquidated
    short_qty: float   # BUY-side liq  = short positions being liquidated


class LiquidationClusterDetector:
    """Groups recent liquidations into price-range buckets.

    Binance forceOrder convention:
        side=SELL → long position being force-sold (long liquidation)
        side=BUY  → short position being force-bought (short liquidation)
    """

    def __init__(
        self,
        window_s: float = 60.0,
        bucket_pct: float = 0.25,
        min_cluster_count: int = 2,
    ) -> None:
        self._window_s = window_s
        self._bucket_pct = bucket_pct / 100.0
        self._min_count = min_cluster_count
        self._records: deque[LiquidationRecord] = deque(maxlen=500)

    def on_liquidation(
        self, side: str, qty: str, price: str, timestamp_ms: int
    ) -> None:
        try:
            self._records.append(
                LiquidationRecord(side=side, qty=float(qty),
                                  price=float(price), timestamp_ms=timestamp_ms)
            )
        except (ValueError, TypeError):
            pass

    def compute_clusters(self, current_price: float, now_ms: int) -> list[LiquidationCluster]:
        if current_price <= 0:
            return []
        cutoff_ms = now_ms - int(self._window_s * 1000)
        recent = [r for r in self._records if r.timestamp_ms >= cutoff_ms]
        if not recent:
            return []
        bucket_size = current_price * self._bucket_pct
        if bucket_size <= 0:
            return []

        buckets: dict[int, list[LiquidationRecord]] = {}
        for rec in recent:
            idx = int(rec.price / bucket_size)
            buckets.setdefault(idx, []).append(rec)

        clusters: list[LiquidationCluster] = []
        for idx, recs in buckets.items():
            if len(recs) < self._min_count:
                continue
            bucket_low = idx * bucket_size
            bucket_high = (idx + 1) * bucket_size
            long_qty = sum(r.qty for r in recs if r.side == "SELL")
            short_qty = sum(r.qty for r in recs if r.side == "BUY")
            clusters.append(LiquidationCluster(
                price_mid=(bucket_low + bucket_high) / 2.0,
                bucket_low=bucket_low,
                bucket_high=bucket_high,
                count=len(recs),
                total_qty=sum(r.qty for r in recs),
                total_usd=sum(r.qty * r.price for r in recs),
                long_qty=long_qty,
                short_qty=short_qty,
            ))
        return sorted(clusters, key=lambda c: c.total_usd, reverse=True)

    def compute_cluster_totals(self, current_price: float, now_ms: int) -> tuple[float, float]:
        """Return (long_liq_usd, short_liq_usd) within the sliding window."""
        cutoff_ms = now_ms - int(self._window_s * 1000)
        recent = [r for r in self._records if r.timestamp_ms >= cutoff_ms]
        long_usd = sum(r.qty * r.price for r in recent if r.side == "SELL")
        short_usd = sum(r.qty * r.price for r in recent if r.side == "BUY")
        return long_usd, short_usd
