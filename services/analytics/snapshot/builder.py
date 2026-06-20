from __future__ import annotations

import json
from decimal import Decimal

from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys
from shared.schemas.analytics import (
    AccountState,
    BookState,
    ExecutionStateSnapshot,
    FlowState,
    FuturesState,
    IndicatorState,
    IndicatorValues,
    MarketState,
    RiskState,
    SnapshotMeta,
    StrategyStateSnapshot,
    UnifiedDecisionSnapshot,
    WallLevel,
)
from shared.schemas.enums import MarketType
from services.analytics.engines.book_analytics import compute_imbalance, compute_spread
from services.analytics.engines.funding import compute_funding_pressure
from services.analytics.engines.indicators import IndicatorValues as _LocalIndicatorValues
from services.analytics.state import SymbolState


def _d(v: float | None) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


def _safe_float(v) -> float | None:
    try:
        f = float(v or 0)
        return f if f != 0.0 else None
    except (ValueError, TypeError):
        return None


class SnapshotBuilder:
    """Builds a UnifiedDecisionSnapshot from SymbolState + Redis lookups.

    All Redis I/O is isolated here so the dispatcher stays synchronous.
    """

    async def build(
        self,
        state: SymbolState,
        redis: RedisClient,
        account_id: str,
        now_ms: int,
    ) -> UnifiedDecisionSnapshot:
        mtype = state.market_type
        sym = state.symbol

        market_state = self._build_market_state(state)

        # Book-dependent analytics require valid integrity state
        if state.integrity.is_valid and state.last_book:
            book_state = self._build_book_state(state, now_ms)
        else:
            book_state = BookState()

        flow_state = self._build_flow_state(state, now_ms)
        futures_state = self._build_futures_state(state, now_ms)

        indicator_state = IndicatorState(by_interval={
            interval: self._to_indicator_values(engine.compute())
            for interval, engine in state.indicators.items()
        })

        account_state = await self._load_account_state(redis, account_id)
        risk_state = await self._load_risk_state(redis, account_id)

        sources = list(state.event_timestamps.keys())
        staleness_ms = {k: now_ms - v for k, v in state.event_timestamps.items()}

        meta = SnapshotMeta(
            snapshot_timestamp_ms=now_ms,
            symbol=sym,
            market_type=MarketType(mtype),
            account_id=account_id or None,
            sources=sources,
            staleness_ms=staleness_ms,
        )

        return UnifiedDecisionSnapshot(
            market_state=market_state,
            book_state=book_state,
            flow_state=flow_state,
            futures_state=futures_state,
            indicator_state=indicator_state,
            account_state=account_state,
            risk_state=risk_state,
            strategy_state=StrategyStateSnapshot(),
            execution_state=ExecutionStateSnapshot(),
            meta=meta,
        )

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_market_state(self, state: SymbolState) -> MarketState:
        ms = MarketState(price=_d(state.last_price))
        bt = state.last_book_ticker
        if bt:
            bid = _safe_float(bt.get("bid_price"))
            ask = _safe_float(bt.get("ask_price"))
            ms.bid = _d(bid)
            ms.ask = _d(ask)
            ms.bid_size = _d(_safe_float(bt.get("bid_qty")))
            ms.ask_size = _d(_safe_float(bt.get("ask_qty")))
            if bid and ask:
                sr = compute_spread(
                    str(bt.get("bid_price")),
                    str(bt.get("ask_price")),
                    str(bt.get("bid_qty")),
                    str(bt.get("ask_qty")),
                )
                if sr:
                    ms.spread = _d(sr.spread)
                    ms.spread_bps = _d(sr.spread_bps)
        return ms

    def _build_book_state(self, state: SymbolState, now_ms: int) -> BookState:
        book = state.last_book or {}
        price = state.last_price or 0.0
        bs = BookState()

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        imb = compute_imbalance(bids, asks, price)
        if imb:
            bs.imbalance_ratio = _d(imb.imbalance_ratio)
            bs.bid_depth_usd = _d(imb.bid_depth_usd)
            bs.ask_depth_usd = _d(imb.ask_depth_usd)

        wr = state.last_walls_result
        if wr:
            bs.spoofing_alert = wr.spoofing_alert
            for w in wr.bid_walls[:5]:
                try:
                    bs.top_bid_walls.append(WallLevel(
                        price=Decimal(w.price),
                        size=Decimal(w.qty),
                        notional_usd=Decimal(str(float(w.price) * float(w.qty))),
                        is_spoofing_candidate=w.is_spoofing_candidate,
                        detected_at_ms=w.first_seen_ms,
                    ))
                except Exception:
                    pass
            for w in wr.ask_walls[:5]:
                try:
                    bs.top_ask_walls.append(WallLevel(
                        price=Decimal(w.price),
                        size=Decimal(w.qty),
                        notional_usd=Decimal(str(float(w.price) * float(w.qty))),
                        is_spoofing_candidate=w.is_spoofing_candidate,
                        detected_at_ms=w.first_seen_ms,
                    ))
                except Exception:
                    pass

        return bs

    def _build_flow_state(self, state: SymbolState, now_ms: int) -> FlowState:
        buy_vol, sell_vol = state.flow.compute_buy_sell_volumes()
        fs = FlowState(
            delta=_d(state.flow.compute_delta()),
            cvd=_d(state.flow.cvd),
            cvd_slope=_d(state.flow.cvd_slope()),
            tape_speed_trades_per_min=_d(state.flow.compute_tape_speed_per_min(now_ms)),
            aggression_ratio=_d(state.flow.compute_aggression_ratio()),
            rvol=_d(state.rvol.compute()),
            buy_volume=_d(buy_vol),
            sell_volume=_d(sell_vol),
        )
        if state.last_price and state.last_price > 0:
            threshold_usd = 10_000.0
            large_buys, large_sells = state.flow.compute_large_trade_stats(
                state.last_price, threshold_usd
            )
            fs.large_trade_threshold_usd = _d(threshold_usd)
            fs.large_buy_count = large_buys
            fs.large_sell_count = large_sells
        return fs

    def _build_futures_state(self, state: SymbolState, now_ms: int) -> FuturesState:
        fut = FuturesState()
        mark = state.last_mark
        if mark:
            mark_price = _safe_float(mark.get("mark_price"))
            index_price = _safe_float(mark.get("index_price"))
            funding_rate = _safe_float(mark.get("funding_rate"))
            fut.mark_price = _d(mark_price)
            fut.index_price = _d(index_price)
            fut.funding_rate = _d(funding_rate)
            nft = mark.get("next_funding_time_ms")
            fut.next_funding_time_ms = int(nft) if nft else None

            if mark_price and index_price and funding_rate is not None:
                fp = compute_funding_pressure(
                    str(mark.get("funding_rate", "")),
                    str(mark.get("mark_price", "")),
                    str(mark.get("index_price", "")),
                )
                fut.funding_pressure_score = _d(fp)

        oi = state.last_oi
        if oi:
            fut.open_interest = _d(_safe_float(oi.get("open_interest")))
            fut.open_interest_value_usd = _d(_safe_float(oi.get("open_interest_value")))

        price = state.last_price
        if price and price > 0:
            long_usd, short_usd = state.liquidations.compute_cluster_totals(price, now_ms)
            if long_usd > 0:
                fut.liquidation_cluster_long_usd = _d(long_usd)
            if short_usd > 0:
                fut.liquidation_cluster_short_usd = _d(short_usd)

        return fut

    def _to_indicator_values(self, v: _LocalIndicatorValues) -> IndicatorValues:
        return IndicatorValues(
            ema_9=_d(v.ema_9),
            ema_21=_d(v.ema_21),
            ema_50=_d(v.ema_50),
            ema_200=_d(v.ema_200),
            rsi_14=_d(v.rsi_14),
            vwap=_d(v.vwap),
            bb_upper=_d(v.bb_upper),
            bb_middle=_d(v.bb_middle),
            bb_lower=_d(v.bb_lower),
            bb_width=_d(v.bb_width),
            atr_14=_d(v.atr_14),
        )

    # ── Redis lookups ─────────────────────────────────────────────────────────

    async def _load_account_state(self, redis: RedisClient, account_id: str) -> AccountState:
        if not account_id:
            return AccountState()
        try:
            raw = await redis.get(RedisKeys.account_snapshot(account_id))
            if raw:
                return AccountState.model_validate_json(raw)
        except Exception:
            pass
        return AccountState()

    async def _load_risk_state(self, redis: RedisClient, account_id: str) -> RiskState:
        if not account_id:
            return RiskState()
        try:
            raw = await redis.get(RedisKeys.risk_state(account_id))
            if raw:
                return RiskState.model_validate_json(raw)
        except Exception:
            pass
        return RiskState()
