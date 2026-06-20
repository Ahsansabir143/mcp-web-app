from __future__ import annotations

from services.analytics.engines.book_analytics import WallDetector, WallsResult
from services.analytics.engines.book_integrity import BookIntegrityState
from services.analytics.engines.flow import FlowEngine
from services.analytics.engines.indicators import IndicatorEngine
from services.analytics.engines.liquidations import LiquidationClusterDetector
from services.analytics.engines.rvol import RvolCalculator


class SymbolState:
    """All in-memory analytics state for a single symbol × market_type pair."""

    def __init__(
        self,
        symbol: str,
        market_type: str,
        flow_cvd_window: int = 1000,
        wall_min_notional: float = 100_000.0,
        wall_depth_levels: int = 20,
        rvol_lookback: int = 20,
        tape_speed_window_s: float = 10.0,
    ) -> None:
        self.symbol = symbol
        self.market_type = market_type

        self.integrity = BookIntegrityState()
        self.flow = FlowEngine(
            cvd_window=flow_cvd_window,
            tape_speed_window_s=tape_speed_window_s,
        )
        self.rvol = RvolCalculator(lookback_buckets=rvol_lookback)
        self.liquidations = LiquidationClusterDetector()
        self.walls = WallDetector(
            min_notional_usd=wall_min_notional,
            depth_levels=wall_depth_levels,
        )
        self.indicators: dict[str, IndicatorEngine] = {}
        self.last_walls_result: WallsResult | None = None

        # Cached market data from last-seen events
        self.last_price: float | None = None
        self.last_book_ticker: dict | None = None
        self.last_mark: dict | None = None
        self.last_oi: dict | None = None
        self.last_book: dict | None = None
        self.last_update_ms: int = 0
        self.event_timestamps: dict[str, int] = {}   # event_type → last timestamp_ms

    def get_indicator(self, interval: str) -> IndicatorEngine:
        if interval not in self.indicators:
            self.indicators[interval] = IndicatorEngine()
        return self.indicators[interval]


class StateStore:
    """Creates and retrieves SymbolState by (symbol, market_type) key."""

    def __init__(
        self,
        flow_cvd_window: int = 1000,
        wall_min_notional: float = 100_000.0,
        wall_depth_levels: int = 20,
        rvol_lookback: int = 20,
        tape_speed_window_s: float = 10.0,
    ) -> None:
        self._params: dict = dict(
            flow_cvd_window=flow_cvd_window,
            wall_min_notional=wall_min_notional,
            wall_depth_levels=wall_depth_levels,
            rvol_lookback=rvol_lookback,
            tape_speed_window_s=tape_speed_window_s,
        )
        self._states: dict[tuple[str, str], SymbolState] = {}

    def get(self, symbol: str, market_type: str) -> SymbolState:
        key = (symbol, market_type)
        if key not in self._states:
            self._states[key] = SymbolState(symbol, market_type, **self._params)
        return self._states[key]

    def all_states(self) -> list[SymbolState]:
        return list(self._states.values())

    def active_symbols(self) -> list[tuple[str, str]]:
        return list(self._states.keys())
