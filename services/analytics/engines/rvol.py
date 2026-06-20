from __future__ import annotations

from collections import deque


class RvolCalculator:
    """Relative volume: current-bucket volume ÷ mean of historical same-period buckets.

    Divides the time axis into fixed-duration buckets.  On each bucket boundary,
    the completed bucket is pushed to historical and a new one starts.
    RVOL > 1.0 means volume is above average; < 1.0 is below.
    """

    def __init__(
        self,
        bucket_duration_s: float = 300.0,
        lookback_buckets: int = 20,
    ) -> None:
        self._bucket_s = max(bucket_duration_s, 1.0)
        self._historical: deque[float] = deque(maxlen=lookback_buckets)
        self._current_volume: float = 0.0
        self._bucket_start_ms: int = 0

    def on_trade(self, qty: str, timestamp_ms: int) -> None:
        try:
            vol = float(qty)
        except (ValueError, TypeError):
            return

        if self._bucket_start_ms == 0:
            self._bucket_start_ms = timestamp_ms

        elapsed_s = (timestamp_ms - self._bucket_start_ms) / 1000.0
        if elapsed_s >= self._bucket_s:
            self._historical.append(self._current_volume)
            self._current_volume = 0.0
            self._bucket_start_ms = timestamp_ms

        self._current_volume += vol

    def compute(self) -> float | None:
        if not self._historical:
            return None
        avg = sum(self._historical) / len(self._historical)
        if avg == 0.0:
            return None
        return self._current_volume / avg
