"""Tests for Phase 7: reconciliation event consumer and loop."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.schemas.enums import EventType, MarketType, Venue
from shared.schemas.events import NormalizedEvent
from services.execution.config import ExecutionSettings
from services.execution.events.publisher import ExecutionEventPublisher
from services.execution.jobs.lifecycle import can_transition, is_terminal
from services.execution.reconciliation.event_consumer import NormalizedEventConsumer
from services.execution.reconciliation.loop import ReconciliationLoop


# ── Helpers ───────────────────────────────────────────────────────────────────


def _settings() -> ExecutionSettings:
    return ExecutionSettings(
        recon_consumer_group="test-recon",
        recon_consumer_name="test-recon-1",
        stale_order_timeout_s=300,
    )


def _publisher() -> tuple[ExecutionEventPublisher, AsyncMock]:
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-0")
    pub = ExecutionEventPublisher(redis)
    return pub, redis


def _mock_job(
    status: str = "acknowledged",
    client_order_id: str = "tp2-abc123",
    symbol: str = "BTCUSDT",
    account_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = status
    job.deterministic_client_order_id = client_order_id
    job.symbol = symbol
    job.account_id = account_id or uuid.uuid4()
    job.trading_mode = "paper"
    job.created_at = created_at or datetime.now(timezone.utc)
    return job


def _mock_order(
    filled_qty: str = "0",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    order_id: uuid.UUID | None = None,
) -> MagicMock:
    order = MagicMock()
    order.id = order_id or uuid.uuid4()
    order.filled_qty = filled_qty
    order.symbol = symbol
    order.side = side
    order.status = "NEW"
    return order


def _mock_repo(job=None, order=None, fill_exists=False) -> AsyncMock:
    repo = AsyncMock()
    repo.get_job_by_client_order_id = AsyncMock(return_value=job)
    repo.get_order_by_client_order_id = AsyncMock(return_value=order)
    repo.get_fill_by_exchange_trade_id = AsyncMock(
        return_value=MagicMock() if fill_exists else None
    )
    repo.insert_fill = AsyncMock(return_value=uuid.uuid4())
    repo.update_order_filled_qty = AsyncMock()
    repo.update_job_status = AsyncMock()
    repo.get_active_jobs = AsyncMock(return_value=[])
    return repo


def _user_order_event(
    client_order_id: str = "tp2-abc",
    exchange_order_id: str = "EX-001",
    order_status: str = "FILLED",
    filled_qty: str = "0.1",
    orig_qty: str = "0.1",
    avg_price: str = "50000",
    trade_time_ms: int = 1000000,
    symbol: str = "BTCUSDT",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_type=EventType.USER_ORDER,
        venue=Venue.BINANCE,
        market_type=MarketType.FUTURES,
        symbol=symbol,
        timestamp_ms=trade_time_ms,
        received_ms=trade_time_ms,
        data={
            "client_order_id": client_order_id,
            "exchange_order_id": exchange_order_id,
            "order_status": order_status,
            "filled_qty": filled_qty,
            "orig_qty": orig_qty,
            "avg_price": avg_price,
            "commission": "0.04",
            "commission_asset": "USDT",
            "realized_pnl": "0",
            "trade_time_ms": trade_time_ms,
        },
    )


def _consumer(repo=None, incident_logger=None, publisher=None) -> NormalizedEventConsumer:
    pub, _ = _publisher() if publisher is None else (publisher, None)
    redis = AsyncMock()
    redis.xack = AsyncMock()
    return NormalizedEventConsumer(
        settings=_settings(),
        redis=redis,
        publisher=pub,
        repository=repo,
        incident_logger=incident_logger,
    )


# ── Orphan / unknown event handling ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_tp2_prefix_emits_orphan():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(),
        redis=AsyncMock(),
        publisher=pub,
    )
    event = _user_order_event(client_order_id="MANUAL-ORDER-001")
    await c._handle_user_order(event)

    assert len(published) == 1
    et, jid, d = published[0]
    assert et == "orphan_exchange_update"
    assert d["reason"] == "not_our_order"
    assert c.orphans_seen == 1


@pytest.mark.asyncio
async def test_unknown_client_order_id_emits_orphan_and_logs_incident():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()
    pub, _ = _publisher()
    pub.publish = capture

    repo = _mock_repo(job=None)
    c = NormalizedEventConsumer(
        settings=_settings(),
        redis=AsyncMock(),
        publisher=pub,
        repository=repo,
        incident_logger=incident_logger,
    )
    event = _user_order_event(client_order_id="tp2-unknownabc123")
    await c._handle_user_order(event)

    assert any(et == "orphan_exchange_update" and d["reason"] == "job_not_found" for et, _, d in published)
    incident_logger.log_incident.assert_called_once()
    assert c.orphans_seen == 1


@pytest.mark.asyncio
async def test_already_terminal_job_emits_orphan():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="filled")
    repo = _mock_repo(job=job)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    event = _user_order_event(client_order_id="tp2-abc", order_status="FILLED")
    await c._handle_user_order(event)

    assert any(et == "orphan_exchange_update" for et, _, _ in published)
    assert c.orphans_seen == 1


@pytest.mark.asyncio
async def test_non_user_order_event_type_is_ignored():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id))

    pub, _ = _publisher()
    pub.publish = capture
    redis = AsyncMock()
    redis.xack = AsyncMock()
    c = NormalizedEventConsumer(settings=_settings(), redis=redis, publisher=pub)

    trade_event = NormalizedEvent(
        event_type=EventType.TRADE,
        venue=Venue.BINANCE,
        market_type=MarketType.FUTURES,
        symbol="BTCUSDT",
        timestamp_ms=1000,
        received_ms=1000,
        data={"trade_id": 1},
    )
    raw = json.dumps({"event": trade_event.model_dump_json()})
    await c._process("1-0", {"event": trade_event.model_dump_json()})

    assert published == []


@pytest.mark.asyncio
async def test_empty_event_field_is_skipped():
    redis = AsyncMock()
    redis.xack = AsyncMock()
    pub, _ = _publisher()
    c = NormalizedEventConsumer(settings=_settings(), redis=redis, publisher=pub)
    await c._process("1-0", {"event": ""})
    redis.xack.assert_called_once()


# ── Cancel handling ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_canceled_status_transitions_job_to_canceled():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="acknowledged")
    repo = _mock_repo(job=job)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    event = _user_order_event(
        client_order_id="tp2-abc", order_status="CANCELED", filled_qty="0"
    )
    await c._handle_user_order(event)

    repo.update_job_status.assert_called_once()
    call_args = repo.update_job_status.call_args
    assert call_args[0][1] == "canceled"
    assert any(et == "job_canceled" for et, _, _ in published)


@pytest.mark.asyncio
async def test_rejected_status_emits_job_canceled():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="submitted")
    repo = _mock_repo(job=job)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    event = _user_order_event(
        client_order_id="tp2-abc", order_status="REJECTED", filled_qty="0"
    )
    await c._handle_user_order(event)

    assert any(et == "job_canceled" for et, _, _ in published)


@pytest.mark.asyncio
async def test_invalid_cancel_transition_emits_mismatch():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="filled")  # terminal — cannot cancel
    repo = _mock_repo(job=job)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    # Call _handle_cancel directly since the terminal check fires first in handle_user_order
    await c._handle_cancel(job, "EX-001", "CANCELED", 1000)

    assert any(et == "reconciliation_mismatch" for et, _, _ in published)
    repo.update_job_status.assert_not_called()


# ── Partial fill handling ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_fill_emits_job_partially_filled():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0")
    repo = _mock_repo(job=job, order=order)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.05",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    assert any(et == "job_partially_filled" for et, _, _ in published)
    assert c.fills_processed == 1


@pytest.mark.asyncio
async def test_partial_fill_inserts_fill_record():
    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0")
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.05",
        orig_qty="0.1",
        trade_time_ms=1234567,
    )
    await c._handle_user_order(event)

    repo.insert_fill.assert_called_once()
    call_kwargs = repo.insert_fill.call_args[1]
    assert call_kwargs["exchange_trade_id"] == "EX-001:t1234567"
    assert call_kwargs["qty"] == Decimal("0.05")


@pytest.mark.asyncio
async def test_partial_fill_updates_order_filled_qty():
    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0")
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.05",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    repo.update_order_filled_qty.assert_called_once()
    call_args = repo.update_order_filled_qty.call_args
    assert call_args[0][1] == Decimal("0.05")  # new cumulative


@pytest.mark.asyncio
async def test_second_partial_fill_computes_leg_qty_from_previous():
    job = _mock_job(status="partially_filled")
    order = _mock_order(filled_qty="0.05")  # already 0.05 filled
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    # New cumulative = 0.08, leg_qty should be 0.03
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.08",
        orig_qty="0.1",
        trade_time_ms=2000000,
    )
    await c._handle_user_order(event)

    repo.insert_fill.assert_called_once()
    call_kwargs = repo.insert_fill.call_args[1]
    assert call_kwargs["qty"] == Decimal("0.03")


@pytest.mark.asyncio
async def test_duplicate_partial_fill_suppressed_by_exchange_trade_id():
    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0")
    repo = _mock_repo(job=job, order=order, fill_exists=True)  # fill already exists
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.05",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    repo.insert_fill.assert_not_called()
    assert c.fills_processed == 0


@pytest.mark.asyncio
async def test_partial_fill_duplicate_via_cumulative_qty_check():
    job = _mock_job(status="partially_filled")
    order = _mock_order(filled_qty="0.05")  # already at 0.05
    repo = _mock_repo(job=job, order=order, fill_exists=False)
    c = _consumer(repo=repo)
    # Same cumulative qty as existing — fill_leg_qty = 0 → skip
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.05",
        orig_qty="0.1",
        trade_time_ms=99999,
    )
    await c._handle_user_order(event)

    repo.insert_fill.assert_not_called()


@pytest.mark.asyncio
async def test_partial_fill_reaching_orig_qty_emits_job_reconciled():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="partially_filled")
    order = _mock_order(filled_qty="0.05")
    repo = _mock_repo(job=job, order=order)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    # Cumulative now equals orig_qty → fully filled
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.1",
        orig_qty="0.1",
        trade_time_ms=3000000,
    )
    await c._handle_user_order(event)

    event_types = [et for et, _, _ in published]
    assert "job_partially_filled" in event_types
    assert "job_reconciled" in event_types


@pytest.mark.asyncio
async def test_partial_fill_missing_order_record_emits_mismatch():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="acknowledged")
    repo = _mock_repo(job=job, order=None)  # no order record
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.05",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    assert any(et == "reconciliation_mismatch" for et, _, _ in published)
    repo.insert_fill.assert_not_called()


@pytest.mark.asyncio
async def test_partial_fill_does_not_duplicate_job_status_when_already_partially_filled():
    job = _mock_job(status="partially_filled")
    order = _mock_order(filled_qty="0.05")
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="PARTIALLY_FILLED",
        filled_qty="0.07",
        orig_qty="0.1",
        trade_time_ms=4000000,
    )
    await c._handle_user_order(event)

    # update_job_status should NOT be called for the partial transition
    # (job is already partially_filled)
    for call in repo.update_job_status.call_args_list:
        assert call[0][1] != "partially_filled"


# ── Full fill handling ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filled_status_emits_job_reconciled():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0")
    repo = _mock_repo(job=job, order=order)
    pub, _ = _publisher()
    pub.publish = capture
    c = NormalizedEventConsumer(
        settings=_settings(), redis=AsyncMock(), publisher=pub, repository=repo
    )
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="FILLED",
        filled_qty="0.1",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    assert any(et == "job_reconciled" for et, _, _ in published)


@pytest.mark.asyncio
async def test_filled_status_transitions_job_to_filled():
    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0")
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="FILLED",
        filled_qty="0.1",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    update_calls = [call[0] for call in repo.update_job_status.call_args_list]
    statuses = [c[1] for c in update_calls]
    assert "filled" in statuses


@pytest.mark.asyncio
async def test_filled_status_with_zero_leg_qty_does_not_insert_duplicate_fill():
    job = _mock_job(status="acknowledged")
    order = _mock_order(filled_qty="0.1")  # already fully filled in DB
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="FILLED",
        filled_qty="0.1",
        orig_qty="0.1",
    )
    await c._handle_user_order(event)

    repo.insert_fill.assert_not_called()


@pytest.mark.asyncio
async def test_filled_from_partially_filled_job_state_transitions_correctly():
    job = _mock_job(status="partially_filled")
    order = _mock_order(filled_qty="0.07")
    repo = _mock_repo(job=job, order=order)
    c = _consumer(repo=repo)
    event = _user_order_event(
        client_order_id="tp2-abc",
        order_status="FILLED",
        filled_qty="0.1",
        orig_qty="0.1",
        trade_time_ms=5000000,
    )
    await c._handle_user_order(event)

    update_calls = [call[0] for call in repo.update_job_status.call_args_list]
    statuses = [c[1] for c in update_calls]
    assert "filled" in statuses


# ── Reconciliation loop ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recon_loop_no_repo_returns_empty_stats():
    pub, _ = _publisher()
    loop = ReconciliationLoop(settings=_settings(), publisher=pub)
    stats = await loop.run_once()
    assert stats["active_jobs"] == 0
    assert stats["stale_detected"] == 0


@pytest.mark.asyncio
async def test_recon_loop_stale_submitted_job_detected():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    pub, _ = _publisher()
    pub.publish = capture

    stale_job = _mock_job(
        status="submitted",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=400),
    )
    repo = _mock_repo()
    repo.get_active_jobs = AsyncMock(return_value=[stale_job])

    loop = ReconciliationLoop(settings=_settings(), publisher=pub, repository=repo)
    stats = await loop.run_once()

    assert stats["stale_detected"] == 1
    assert any(et == "stale_order_detected" for et, _, _ in published)
    assert loop.total_stale_detected == 1


@pytest.mark.asyncio
async def test_recon_loop_recent_submitted_job_not_stale():
    published = []

    async def capture(event_type, job_id, data=None):
        published.append((event_type, job_id, data))

    pub, _ = _publisher()
    pub.publish = capture

    recent_job = _mock_job(
        status="submitted",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    repo = _mock_repo()
    repo.get_active_jobs = AsyncMock(return_value=[recent_job])

    loop = ReconciliationLoop(settings=_settings(), publisher=pub, repository=repo)
    stats = await loop.run_once()

    assert stats["stale_detected"] == 0
    assert not any(et == "stale_order_detected" for et, _, _ in published)


@pytest.mark.asyncio
async def test_recon_loop_partially_filled_counted_in_stats():
    pub, _ = _publisher()
    job = _mock_job(status="partially_filled")
    repo = _mock_repo()
    repo.get_active_jobs = AsyncMock(return_value=[job])

    loop = ReconciliationLoop(settings=_settings(), publisher=pub, repository=repo)
    stats = await loop.run_once()

    assert stats["partially_filled_pending"] == 1
    assert stats["stale_detected"] == 0


@pytest.mark.asyncio
async def test_recon_loop_logs_incident_for_stale_job():
    pub, _ = _publisher()

    async def capture(event_type, job_id, data=None):
        pass

    pub.publish = capture
    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    stale_job = _mock_job(
        status="submitted",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=400),
    )
    repo = _mock_repo()
    repo.get_active_jobs = AsyncMock(return_value=[stale_job])

    loop = ReconciliationLoop(
        settings=_settings(),
        publisher=pub,
        repository=repo,
        incident_logger=incident_logger,
    )
    await loop.run_once()

    incident_logger.log_incident.assert_called_once()
    call_kwargs = incident_logger.log_incident.call_args
    assert call_kwargs[0][0] == "stale_order"


@pytest.mark.asyncio
async def test_recon_loop_total_scans_increments():
    pub, _ = _publisher()
    repo = _mock_repo()
    loop = ReconciliationLoop(settings=_settings(), publisher=pub, repository=repo)
    await loop.run_once()
    await loop.run_once()
    assert loop.total_scans == 2


@pytest.mark.asyncio
async def test_recon_loop_multiple_active_jobs_all_scanned():
    pub, _ = _publisher()
    jobs = [
        _mock_job(
            status="submitted",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        ),
        _mock_job(status="partially_filled"),
        _mock_job(status="acknowledged"),
    ]
    repo = _mock_repo()
    repo.get_active_jobs = AsyncMock(return_value=jobs)

    loop = ReconciliationLoop(settings=_settings(), publisher=pub, repository=repo)
    stats = await loop.run_once()

    assert stats["active_jobs"] == 3
    assert stats["stale_detected"] == 0
    assert stats["partially_filled_pending"] == 1


# ── Lifecycle transitions (used by reconciliation) ────────────────────────────


def test_acknowledged_can_transition_to_partially_filled():
    assert can_transition("acknowledged", "partially_filled") is True


def test_acknowledged_can_transition_to_filled():
    assert can_transition("acknowledged", "filled") is True


def test_acknowledged_can_transition_to_canceled():
    assert can_transition("acknowledged", "canceled") is True


def test_partially_filled_can_transition_to_filled():
    assert can_transition("partially_filled", "filled") is True


def test_partially_filled_cannot_transition_to_itself():
    assert can_transition("partially_filled", "partially_filled") is False


def test_partially_filled_can_transition_to_canceled():
    assert can_transition("partially_filled", "canceled") is True


def test_filled_is_terminal():
    assert is_terminal("filled") is True


def test_canceled_is_terminal():
    assert is_terminal("canceled") is True


def test_submitted_is_not_terminal():
    assert is_terminal("submitted") is False


def test_acknowledged_is_not_terminal():
    assert is_terminal("acknowledged") is False
