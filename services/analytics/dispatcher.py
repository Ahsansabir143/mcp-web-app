from __future__ import annotations

from shared.schemas.enums import EventType
from shared.schemas.events import NormalizedEvent
from services.analytics.state import StateStore, SymbolState


class AnalyticsDispatcher:
    """Synchronously updates SymbolState from a NormalizedEvent.

    Called from the async consumer on every incoming event; all updates are
    pure in-memory — no I/O happens here.
    """

    def __init__(self, store: StateStore) -> None:
        self._store = store

    def update(self, event: NormalizedEvent, current_book: dict | None = None) -> None:
        state = self._store.get(event.symbol, event.market_type.value)
        state.last_update_ms = event.timestamp_ms
        state.event_timestamps[event.event_type.value] = event.timestamp_ms

        et = event.event_type
        d = event.data

        if et == EventType.TRADE:
            self._on_trade(state, d, event.timestamp_ms)
        elif et == EventType.AGG_TRADE:
            self._on_agg_trade(state, d, event.timestamp_ms)
        elif et == EventType.BOOK_TICKER:
            self._on_book_ticker(state, d)
        elif et == EventType.ORDERBOOK_SNAPSHOT:
            self._on_orderbook_snapshot(state, d, current_book, event.timestamp_ms)
        elif et == EventType.ORDERBOOK_DELTA:
            self._on_orderbook_delta(state, d, current_book, event.timestamp_ms)
        elif et == EventType.KLINE:
            self._on_kline(state, d)
        elif et == EventType.MARK_PRICE:
            self._on_mark_price(state, d)
        elif et == EventType.LIQUIDATION:
            self._on_liquidation(state, d, event.timestamp_ms)

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_trade(self, state: SymbolState, d: dict, ts: int) -> None:
        price = d.get("price", "")
        qty = d.get("qty", "")
        is_buyer_maker = bool(d.get("is_buyer_maker", False))
        state.flow.on_trade(price, qty, is_buyer_maker, ts)
        state.rvol.on_trade(qty, ts)
        try:
            p = float(price)
            if p > 0:
                state.last_price = p
        except (ValueError, TypeError):
            pass

    def _on_agg_trade(self, state: SymbolState, d: dict, ts: int) -> None:
        price = d.get("price", "")
        qty = d.get("qty", "")
        is_buyer_maker = bool(d.get("is_buyer_maker", False))
        state.flow.on_trade(price, qty, is_buyer_maker, ts)
        state.rvol.on_trade(qty, ts)
        try:
            p = float(price)
            if p > 0:
                state.last_price = p
        except (ValueError, TypeError):
            pass

    def _on_book_ticker(self, state: SymbolState, d: dict) -> None:
        state.last_book_ticker = d
        try:
            bid = float(d.get("bid_price", 0) or 0)
            ask = float(d.get("ask_price", 0) or 0)
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else bid or ask
            if mid > 0:
                state.last_price = mid
        except (ValueError, TypeError):
            pass

    def _on_orderbook_snapshot(
        self,
        state: SymbolState,
        d: dict,
        current_book: dict | None,
        ts: int,
    ) -> None:
        last_update_id = int(d.get("last_update_id", 0))
        state.integrity.on_snapshot(last_update_id, ts)
        if current_book:
            state.last_book = current_book
        if state.last_book and state.integrity.is_valid:
            self._refresh_walls(state, ts)

    def _on_orderbook_delta(
        self,
        state: SymbolState,
        d: dict,
        current_book: dict | None,
        ts: int,
    ) -> None:
        first_uid = int(d.get("first_update_id", 0))
        last_uid = int(d.get("last_update_id", 0))
        valid = state.integrity.on_delta(first_uid, last_uid, ts)
        if valid and current_book:
            state.last_book = current_book
            self._refresh_walls(state, ts)

    def _on_kline(self, state: SymbolState, d: dict) -> None:
        interval = str(d.get("interval", ""))
        if not interval:
            return
        if not bool(d.get("is_closed", False)):
            return
        engine = state.get_indicator(interval)
        engine.on_kline_close(
            open_=str(d.get("open", "0")),
            high=str(d.get("high", "0")),
            low=str(d.get("low", "0")),
            close=str(d.get("close", "0")),
            volume=str(d.get("volume", "0")),
        )

    def _on_mark_price(self, state: SymbolState, d: dict) -> None:
        state.last_mark = d
        try:
            mark = float(d.get("mark_price", 0) or 0)
            if mark > 0:
                state.last_price = mark
        except (ValueError, TypeError):
            pass

    def _on_liquidation(self, state: SymbolState, d: dict, ts: int) -> None:
        state.liquidations.on_liquidation(
            side=str(d.get("side", "")),
            qty=str(d.get("orig_qty", "0")),
            price=str(d.get("price", "0")),
            timestamp_ms=ts,
        )

    def _refresh_walls(self, state: SymbolState, ts: int) -> None:
        if not state.last_book or not state.last_price:
            return
        bids = state.last_book.get("bids", [])
        asks = state.last_book.get("asks", [])
        state.last_walls_result = state.walls.update(bids, asks, state.last_price, ts)
