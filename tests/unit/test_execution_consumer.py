"""Tests for client_order_id, job lifecycle, paper adapter, event publisher, and consumer routing."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from shared.schemas.enums import MarketType, OrderSide, OrderType, TradingMode, ApprovalLevel
from shared.schemas.execution import ExecutionRequest, RiskDecision
from shared.schemas.strategy import TradeIntent
from services.execution.adapter.paper import PaperExecutionAdapter
from services.execution.consumer import ExecutionConsumer
from services.execution.events.publisher import ExecutionEventPublisher
from services.execution.jobs.client_order_id import make_client_order_id
from services.execution.jobs.lifecycle import (
    VALID_JOB_TRANSITIONS,
    JobLifecycleError,
    assert_transition,
    can_transition,
    is_terminal,
)
from services.execution.risk.engine import ExecutionRiskEngine


# ── Helpers ───────────────────────────────────────────────────────────────────


def _intent(
    symbol: str = "BTCUSDT",
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("0.01"),
    size_usd: Decimal = Decimal("500"),
    limit_price: Decimal | None = Decimal("50000"),
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        market_type=MarketType.FUTURES,
        side=side,
        size=size,
        size_usd=size_usd,
        limit_price=limit_price,
    )


def _request(intent: TradeIntent | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        trade_intent=intent or _intent(),
        user_id="user-1",
        account_id="acct-1",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
    )


def _passing_risk_engine() -> AsyncMock:
    engine = AsyncMock(spec=ExecutionRiskEngine)
    engine.evaluate = AsyncMock(return_value=RiskDecision(passed=True, checks={}, failures=[]))
    return engine


def _blocking_risk_engine(reason: str = "kill_switch_active") -> AsyncMock:
    engine = AsyncMock(spec=ExecutionRiskEngine)
    engine.evaluate = AsyncMock(
        return_value=RiskDecision(passed=False, checks={}, failures=[reason])
    )
    return engine


def _mock_redis(lock_acquired: bool = True) -> AsyncMock:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True if lock_acquired else None)
    redis.xadd = AsyncMock(return_value="1-1")
    redis.xack = AsyncMock()
    return redis


def _make_consumer(
    redis=None,
    risk_engine=None,
    lock_acquired: bool = True,
) -> ExecutionConsumer:
    r = redis or _mock_redis(lock_acquired)
    re = risk_engine or _passing_risk_engine()
    consumer = ExecutionConsumer(redis=r, repository=None, risk_engine=re)
    return consumer


def _intent_fields(intent: TradeIntent | None = None) -> dict:
    i = intent or _intent()
    return {"intent": i.model_dump_json()}


# ── client_order_id ───────────────────────────────────────────────────────────


def test_client_order_id_deterministic():
    cid1 = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "BUY")
    cid2 = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "BUY")
    assert cid1 == cid2


def test_client_order_id_different_intent():
    cid1 = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "BUY")
    cid2 = make_client_order_id("intent-2", "acct-1", "BTCUSDT", "BUY")
    assert cid1 != cid2


def test_client_order_id_different_account():
    cid1 = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "BUY")
    cid2 = make_client_order_id("intent-1", "acct-2", "BTCUSDT", "BUY")
    assert cid1 != cid2


def test_client_order_id_different_side():
    buy_cid = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "BUY")
    sell_cid = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "SELL")
    assert buy_cid != sell_cid


def test_client_order_id_length_within_binance_limit():
    cid = make_client_order_id("intent-abc", "acct-1", "BTCUSDT", "BUY")
    assert len(cid) <= 36


def test_client_order_id_format():
    cid = make_client_order_id("intent-1", "acct-1", "BTCUSDT", "BUY")
    assert cid.startswith("tp2-")
    assert len(cid) == 36


# ── Job lifecycle transitions ─────────────────────────────────────────────────


@pytest.mark.parametrize("from_state,to_state", [
    ("queued", "approved"),
    ("queued", "blocked"),
    ("queued", "failed"),
    ("approved", "submitted"),
    ("approved", "failed"),
    ("submitted", "acknowledged"),
    ("submitted", "canceled"),
    ("submitted", "failed"),
    ("acknowledged", "filled"),
    ("acknowledged", "partially_filled"),
    ("acknowledged", "canceled"),
    ("acknowledged", "failed"),
    ("partially_filled", "filled"),
    ("partially_filled", "canceled"),
    ("partially_filled", "failed"),
    ("failed", "rolled_back"),
])
def test_valid_job_transition(from_state, to_state):
    assert can_transition(from_state, to_state) is True
    assert_transition(from_state, to_state)  # no exception


@pytest.mark.parametrize("from_state,to_state", [
    ("blocked", "approved"),   # blocked is terminal
    ("filled", "approved"),    # filled is terminal
    ("canceled", "submitted"), # canceled is terminal
    ("rolled_back", "queued"), # rolled_back is terminal
    ("queued", "filled"),      # must go through approved first
    ("approved", "filled"),    # must go through submitted
])
def test_invalid_job_transition_raises(from_state, to_state):
    assert can_transition(from_state, to_state) is False
    with pytest.raises(JobLifecycleError):
        assert_transition(from_state, to_state)


@pytest.mark.parametrize("state", ["blocked", "filled", "canceled", "rolled_back"])
def test_terminal_states(state):
    assert is_terminal(state) is True


@pytest.mark.parametrize("state", ["queued", "approved", "submitted", "acknowledged", "partially_filled", "failed"])
def test_non_terminal_states(state):
    assert is_terminal(state) is False


# ── PaperExecutionAdapter ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_adapter_success():
    adapter = PaperExecutionAdapter()
    request = _request()
    response = await adapter.submit(request, "tp2-testclientorderid0000000000000")
    assert response.success is True


@pytest.mark.asyncio
async def test_paper_adapter_deterministic_exchange_id():
    adapter = PaperExecutionAdapter()
    cid = "tp2-testclientorderid0000000000000"
    r1 = await adapter.submit(_request(), cid)
    r2 = await adapter.submit(_request(), cid)
    assert r1.exchange_order_id == r2.exchange_order_id


@pytest.mark.asyncio
async def test_paper_adapter_exchange_id_starts_with_paper():
    adapter = PaperExecutionAdapter()
    response = await adapter.submit(_request(), "tp2-abc")
    assert response.exchange_order_id is not None
    assert response.exchange_order_id.startswith("PAPER-")


@pytest.mark.asyncio
async def test_paper_adapter_fill_price_from_limit_price():
    adapter = PaperExecutionAdapter()
    intent = _intent(limit_price=Decimal("50000"))
    response = await adapter.submit(_request(intent), "tp2-abc")
    assert response.fill_price == Decimal("50000")


@pytest.mark.asyncio
async def test_paper_adapter_fill_price_derived_when_no_limit():
    adapter = PaperExecutionAdapter()
    # size=0.01 BTC, size_usd=500 → derived price = 50000
    intent = _intent(size=Decimal("0.01"), size_usd=Decimal("500"), limit_price=None)
    response = await adapter.submit(_request(intent), "tp2-abc")
    assert response.fill_price is not None
    assert float(response.fill_price) == pytest.approx(50000.0, rel=1e-4)


@pytest.mark.asyncio
async def test_paper_adapter_commission_positive():
    adapter = PaperExecutionAdapter()
    response = await adapter.submit(_request(), "tp2-abc")
    assert response.commission is not None
    assert response.commission > 0


@pytest.mark.asyncio
async def test_paper_adapter_name():
    assert PaperExecutionAdapter().adapter_name() == "paper"


@pytest.mark.asyncio
async def test_paper_adapter_raw_response_has_mode():
    adapter = PaperExecutionAdapter()
    response = await adapter.submit(_request(), "tp2-abc")
    assert response.raw_response is not None
    assert response.raw_response.get("mode") == "paper"


# ── ExecutionEventPublisher ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publisher_calls_xadd():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    pub = ExecutionEventPublisher(redis)
    await pub.publish("job_queued", "job-1", {"symbol": "BTCUSDT"})
    redis.xadd.assert_called_once()


@pytest.mark.asyncio
async def test_publisher_stream_is_execution_events():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    pub = ExecutionEventPublisher(redis)
    await pub.publish("job_filled", "job-1", {})
    from shared.redis.streams import StreamNames
    assert redis.xadd.call_args[0][0] == StreamNames.EXECUTION_EVENTS


@pytest.mark.asyncio
async def test_publisher_fields_contain_event_type():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    pub = ExecutionEventPublisher(redis)
    await pub.publish("kill_switch_blocked", "job-1", {"reason": "emergency"})
    fields = redis.xadd.call_args[0][1]
    assert fields["event_type"] == "kill_switch_blocked"
    assert fields["job_id"] == "job-1"
    assert "timestamp_ms" in fields


@pytest.mark.asyncio
async def test_publisher_data_is_json():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    pub = ExecutionEventPublisher(redis)
    await pub.publish("job_blocked", "job-1", {"failures": ["kill_switch_active"]})
    fields = redis.xadd.call_args[0][1]
    parsed = json.loads(fields["data"])
    assert parsed["failures"] == ["kill_switch_active"]


# ── Consumer routing ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consumer_emits_job_queued_and_filled_on_success():
    consumer = _make_consumer()
    fields = _intent_fields()

    event_types = []
    async def capture_publish(event_type, job_id, data=None):
        event_types.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture_publish):
        await consumer._process("1-1", fields)

    assert "job_queued" in event_types
    assert "job_approved" in event_types
    assert "job_submitted" in event_types
    assert "job_acknowledged" in event_types
    assert "job_filled" in event_types


@pytest.mark.asyncio
async def test_consumer_emits_job_blocked_when_risk_fails():
    consumer = _make_consumer(risk_engine=_blocking_risk_engine("kill_switch_active"))
    fields = _intent_fields()

    event_types = []
    async def capture(event_type, job_id, data=None):
        event_types.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture):
        await consumer._process("1-1", fields)

    assert "job_queued" in event_types
    assert "kill_switch_blocked" in event_types
    assert "job_filled" not in event_types


@pytest.mark.asyncio
async def test_consumer_kill_switch_failure_uses_specific_event_type():
    consumer = _make_consumer(risk_engine=_blocking_risk_engine("kill_switch_active"))
    fields = _intent_fields()

    published = []
    async def capture(event_type, job_id, data=None):
        published.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture):
        await consumer._process("1-1", fields)

    assert "kill_switch_blocked" in published


@pytest.mark.asyncio
async def test_consumer_cooldown_failure_uses_specific_event_type():
    consumer = _make_consumer(risk_engine=_blocking_risk_engine("symbol_on_cooldown"))
    fields = _intent_fields()

    published = []
    async def capture(event_type, job_id, data=None):
        published.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture):
        await consumer._process("1-1", fields)

    assert "cooldown_blocked" in published


@pytest.mark.asyncio
async def test_consumer_skips_empty_intent_field():
    consumer = _make_consumer()
    published = []
    async def capture(event_type, job_id, data=None):
        published.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture):
        await consumer._process("1-1", {"intent": ""})

    assert published == []


@pytest.mark.asyncio
async def test_consumer_skips_malformed_intent():
    consumer = _make_consumer()
    published = []
    async def capture(event_type, job_id, data=None):
        published.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture):
        await consumer._process("1-1", {"intent": "{not valid json}"})

    assert published == []


@pytest.mark.asyncio
async def test_consumer_duplicate_detection():
    """Second delivery of same intent_id→ lock not acquired → duplicate_blocked."""
    redis = _mock_redis(lock_acquired=True)
    # First call acquires; second call simulates lock already held (return None)
    call_count = [0]
    async def mock_set(*args, **kwargs):
        call_count[0] += 1
        return True if call_count[0] == 1 else None

    redis.set = AsyncMock(side_effect=mock_set)

    consumer = _make_consumer(redis=redis)
    intent = _intent()
    fields = {"intent": intent.model_dump_json()}

    published_first = []
    published_second = []

    async def capture1(event_type, job_id, data=None):
        published_first.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture1):
        await consumer._process("1-1", fields)

    async def capture2(event_type, job_id, data=None):
        published_second.append(event_type)

    with patch.object(consumer._publisher, "publish", side_effect=capture2):
        await consumer._process("1-2", fields)

    assert "duplicate_blocked" in published_second
    assert "job_filled" not in published_second


@pytest.mark.asyncio
async def test_consumer_increments_jobs_processed():
    consumer = _make_consumer()
    assert consumer.jobs_processed == 0
    fields = _intent_fields()
    await consumer._process("1-1", fields)
    assert consumer.jobs_processed == 1


@pytest.mark.asyncio
async def test_consumer_increments_jobs_blocked():
    consumer = _make_consumer(risk_engine=_blocking_risk_engine())
    assert consumer.jobs_blocked == 0
    await consumer._process("1-1", _intent_fields())
    assert consumer.jobs_blocked == 1


@pytest.mark.asyncio
async def test_consumer_sets_cooldown_after_fill():
    """After a successful fill the symbol cooldown must be set."""
    consumer = _make_consumer()
    fields = _intent_fields()
    with patch.object(consumer._cooldown, "set_cooldown", new_callable=AsyncMock) as mock_cd:
        await consumer._process("1-1", fields)
    mock_cd.assert_called_once()
    call_args = mock_cd.call_args[0]
    assert call_args[1] == "BTCUSDT"


# ── credentials ───────────────────────────────────────────────────────────────


def test_credential_encrypt_decrypt_roundtrip():
    from services.execution.credentials import decrypt_credential, encrypt_credential
    plaintext = "super-secret-api-key-123"
    secret_key = "test-secret"
    ct, iv = encrypt_credential(plaintext, secret_key)
    recovered = decrypt_credential(ct, iv, secret_key)
    assert recovered == plaintext


def test_credential_encrypt_produces_different_iv_each_time():
    from services.execution.credentials import encrypt_credential
    ct1, iv1 = encrypt_credential("key", "secret")
    ct2, iv2 = encrypt_credential("key", "secret")
    assert iv1 != iv2  # random IV per call
    assert ct1 != ct2  # therefore different ciphertext


def test_credential_wrong_key_raises():
    from services.execution.credentials import decrypt_credential, encrypt_credential
    ct, iv = encrypt_credential("my-key", "correct-secret")
    with pytest.raises(ValueError, match="decryption"):
        decrypt_credential(ct, iv, "wrong-secret")


def test_credential_tampered_ciphertext_raises():
    from services.execution.credentials import decrypt_credential, encrypt_credential
    import base64
    ct_b64, iv_b64 = encrypt_credential("my-key", "secret")
    ct_bytes = bytearray(base64.b64decode(ct_b64))
    ct_bytes[0] ^= 0xFF  # flip bits
    with pytest.raises(ValueError):
        decrypt_credential(base64.b64encode(bytes(ct_bytes)).decode(), iv_b64, "secret")
