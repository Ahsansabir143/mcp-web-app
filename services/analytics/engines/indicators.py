from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class IndicatorValues:
    """Indicator snapshot for one kline interval (float precision)."""

    ema_9: float | None = None
    ema_21: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    rsi_14: float | None = None
    vwap: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_width: float | None = None
    atr_14: float | None = None


class IndicatorEngine:
    """Incremental OHLCV-based indicator engine for one kline interval.

    Computes EMA(9/21/50/200), RSI(14), VWAP, Bollinger Bands(20), ATR(14).
    All calculations are incremental — no full-history recomputation per bar.
    """

    EMA_PERIODS = (9, 21, 50, 200)
    RSI_PERIOD = 14
    BB_PERIOD = 20
    ATR_PERIOD = 14
    MAX_HISTORY = 250

    def __init__(self) -> None:
        self._closes: deque[float] = deque(maxlen=self.MAX_HISTORY)
        self._highs: deque[float] = deque(maxlen=self.MAX_HISTORY)
        self._lows: deque[float] = deque(maxlen=self.MAX_HISTORY)
        self._volumes: deque[float] = deque(maxlen=self.MAX_HISTORY)

        # EMA incremental state
        self._ema: dict[int, float | None] = {p: None for p in self.EMA_PERIODS}

        # RSI incremental (Wilder's smoothing)
        self._rsi_avg_gain: float | None = None
        self._rsi_avg_loss: float | None = None

        # ATR incremental
        self._tr_buffer: deque[float] = deque(maxlen=self.ATR_PERIOD + 5)
        self._atr: float | None = None
        self._prev_close: float | None = None

        # VWAP (reset manually on daily open)
        self._vwap_num: float = 0.0   # Σ(typical_price × volume)
        self._vwap_den: float = 0.0   # Σ(volume)

    # ── Public API ────────────────────────────────────────────────────────────

    def on_kline_close(
        self,
        open_: str,
        high: str,
        low: str,
        close: str,
        volume: str,
    ) -> None:
        try:
            c = float(close)
            h = float(high)
            lo = float(low)
            v = float(volume)
        except (ValueError, TypeError):
            return

        self._closes.append(c)
        self._highs.append(h)
        self._lows.append(lo)
        self._volumes.append(v)

        self._update_emas(c)
        self._update_rsi(c)
        self._update_atr(h, lo, c)
        self._update_vwap(h, lo, c, v)
        self._prev_close = c

    def reset_vwap(self) -> None:
        """Call at the start of each daily session."""
        self._vwap_num = 0.0
        self._vwap_den = 0.0

    def compute(self) -> IndicatorValues:
        vals = IndicatorValues(
            ema_9=self._ema.get(9),
            ema_21=self._ema.get(21),
            ema_50=self._ema.get(50),
            ema_200=self._ema.get(200),
        )

        # RSI
        if self._rsi_avg_gain is not None and self._rsi_avg_loss is not None:
            if self._rsi_avg_loss == 0.0:
                vals.rsi_14 = 100.0
            else:
                rs = self._rsi_avg_gain / self._rsi_avg_loss
                vals.rsi_14 = 100.0 - 100.0 / (1.0 + rs)

        # VWAP
        if self._vwap_den > 0.0:
            vals.vwap = self._vwap_num / self._vwap_den

        # Bollinger Bands
        if len(self._closes) >= self.BB_PERIOD:
            recent = list(self._closes)[-self.BB_PERIOD:]
            sma = sum(recent) / self.BB_PERIOD
            variance = sum((x - sma) ** 2 for x in recent) / self.BB_PERIOD
            std = math.sqrt(variance)
            vals.bb_middle = sma
            vals.bb_upper = sma + 2.0 * std
            vals.bb_lower = sma - 2.0 * std
            vals.bb_width = (vals.bb_upper - vals.bb_lower) / sma if sma != 0.0 else None

        vals.atr_14 = self._atr
        return vals

    # ── Private updaters ──────────────────────────────────────────────────────

    def _update_emas(self, close: float) -> None:
        for period in self.EMA_PERIODS:
            if self._ema[period] is None:
                if len(self._closes) >= period:
                    seed = list(self._closes)[-period:]
                    self._ema[period] = sum(seed) / period
            else:
                k = 2.0 / (period + 1)
                self._ema[period] = close * k + self._ema[period] * (1.0 - k)

    def _update_rsi(self, close: float) -> None:
        if self._prev_close is None:
            return
        change = close - self._prev_close
        gain = max(0.0, change)
        loss = max(0.0, -change)

        if self._rsi_avg_gain is None:
            # Seed on exactly RSI_PERIOD+1 bars (gives RSI_PERIOD price changes)
            if len(self._closes) == self.RSI_PERIOD + 1:
                closes_list = list(self._closes)
                gains = [max(0.0, closes_list[i] - closes_list[i - 1])
                         for i in range(1, len(closes_list))]
                losses = [max(0.0, closes_list[i - 1] - closes_list[i])
                          for i in range(1, len(closes_list))]
                self._rsi_avg_gain = sum(gains) / self.RSI_PERIOD
                self._rsi_avg_loss = sum(losses) / self.RSI_PERIOD
        else:
            n = self.RSI_PERIOD
            self._rsi_avg_gain = (self._rsi_avg_gain * (n - 1) + gain) / n
            self._rsi_avg_loss = (self._rsi_avg_loss * (n - 1) + loss) / n

    def _update_atr(self, high: float, low: float, close: float) -> None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - self._prev_close),
                abs(low - self._prev_close),
            )
        self._tr_buffer.append(tr)

        if self._atr is None:
            if len(self._tr_buffer) >= self.ATR_PERIOD:
                self._atr = sum(list(self._tr_buffer)[-self.ATR_PERIOD:]) / self.ATR_PERIOD
        else:
            self._atr = (self._atr * (self.ATR_PERIOD - 1) + tr) / self.ATR_PERIOD

    def _update_vwap(self, high: float, low: float, close: float, volume: float) -> None:
        typical = (high + low + close) / 3.0
        self._vwap_num += typical * volume
        self._vwap_den += volume
