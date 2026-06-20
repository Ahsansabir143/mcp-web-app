"""Tests for Phase 7: account context loader and state reader."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.account.context import AccountContext, AccountContextLoader
from services.execution.account.state_reader import AccountStateReader
from services.execution.credentials import decrypt_credential, encrypt_credential
from shared.risk.limits import RiskLimits
from shared.schemas.enums import ApprovalLevel, TradingMode


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_account(
    trading_mode: str = "paper",
    approval_level: str = "l2_paper",
    user_id: uuid.UUID | None = None,
) -> MagicMock:
    account = MagicMock()
    account.user_id = user_id or uuid.uuid4()
    account.trading_mode = trading_mode
    account.approval_level = approval_level
    account.is_active = True
    return account


def _make_approval(
    level: str = "l2_paper",
    paper_only: bool = True,
    allowed_symbols: list | None = None,
    denied_symbols: list | None = None,
) -> MagicMock:
    rec = MagicMock()
    rec.level = level
    rec.paper_only = paper_only
    rec.allowed_symbols = allowed_symbols
    rec.denied_symbols = denied_symbols or []
    return rec


def _make_policy(
    max_position_size_usd: str = "2000",
    max_daily_loss_usd: str = "500",
    max_concurrent_positions: int = 5,
    symbol_cooldown_seconds: int = 600,
) -> MagicMock:
    pol = MagicMock()
    pol.max_position_size_usd = max_position_size_usd
    pol.max_daily_loss_usd = max_daily_loss_usd
    pol.max_concurrent_positions = max_concurrent_positions
    pol.symbol_cooldown_seconds = symbol_cooldown_seconds
    return pol


def _session_factory(
    account=None,
    approval=None,
    policy=None,
    cred=None,
) -> MagicMock:
    """Build an async session factory mock wired to return the provided fixtures."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=account)

    # We need different scalars for different queries
    # Order: approval, policy, cred
    _results = [approval, policy, cred]
    _call_count = [0]

    async def _execute(stmt):
        result = MagicMock()
        idx = _call_count[0]
        _call_count[0] += 1
        val = _results[idx] if idx < len(_results) else None
        result.scalar_one_or_none = MagicMock(return_value=val)
        return result

    session.execute = _execute

    ctx_manager = MagicMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=session)
    ctx_manager.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock()
    factory.return_value = ctx_manager
    return factory


# ── AccountContextLoader.load ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_returns_none_for_invalid_uuid():
    loader = AccountContextLoader(session_factory=MagicMock(), secret_key="k")
    result = await loader.load("not-a-uuid")
    assert result is None


@pytest.mark.asyncio
async def test_load_returns_none_when_account_not_found():
    factory = _session_factory(account=None)
    loader = AccountContextLoader(session_factory=factory)
    result = await loader.load(str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_load_returns_context_with_defaults_when_no_policy():
    account = _make_account()
    factory = _session_factory(account=account, approval=None, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx is not None
    assert ctx.paper_only is True
    assert ctx.has_credentials is False
    assert ctx.allowed_symbols is None
    assert isinstance(ctx.limits, RiskLimits)


@pytest.mark.asyncio
async def test_load_picks_approval_level_from_approval_record():
    account = _make_account(approval_level="l0_readonly")
    approval = _make_approval(level="l3_assisted_live", paper_only=False)
    factory = _session_factory(account=account, approval=approval, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.approval_level == ApprovalLevel.L3_ASSISTED_LIVE
    assert ctx.paper_only is False


@pytest.mark.asyncio
async def test_load_falls_back_to_account_approval_level_when_no_record():
    account = _make_account(approval_level="l2_paper")
    factory = _session_factory(account=account, approval=None, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.approval_level == ApprovalLevel.L2_PAPER


@pytest.mark.asyncio
async def test_load_has_credentials_true_when_cred_ref_exists():
    account = _make_account()
    cred = MagicMock()  # non-None → has_credentials=True
    factory = _session_factory(account=account, approval=None, policy=None, cred=cred)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.has_credentials is True


@pytest.mark.asyncio
async def test_load_has_credentials_false_when_no_cred_ref():
    account = _make_account()
    factory = _session_factory(account=account, approval=None, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.has_credentials is False


@pytest.mark.asyncio
async def test_load_risk_policy_sets_limits():
    account = _make_account()
    policy = _make_policy(
        max_position_size_usd="5000",
        max_daily_loss_usd="1000",
        max_concurrent_positions=10,
        symbol_cooldown_seconds=900,
    )
    factory = _session_factory(account=account, approval=None, policy=policy, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.limits.max_position_size_usd == Decimal("5000")
    assert ctx.limits.max_daily_loss_usd == Decimal("1000")
    assert ctx.limits.max_concurrent_positions == 10
    assert ctx.limits.symbol_cooldown_seconds == 900


@pytest.mark.asyncio
async def test_load_allowed_symbols_from_approval():
    account = _make_account()
    approval = _make_approval(allowed_symbols=["BTCUSDT", "ETHUSDT"])
    factory = _session_factory(account=account, approval=approval, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.allowed_symbols == ["BTCUSDT", "ETHUSDT"]


@pytest.mark.asyncio
async def test_load_denied_symbols_from_approval():
    account = _make_account()
    approval = _make_approval(denied_symbols=["SHIBUSDT"])
    factory = _session_factory(account=account, approval=approval, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert "SHIBUSDT" in ctx.denied_symbols


@pytest.mark.asyncio
async def test_load_trading_mode_paper():
    account = _make_account(trading_mode="paper")
    factory = _session_factory(account=account, approval=None, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.trading_mode == TradingMode.PAPER


@pytest.mark.asyncio
async def test_load_trading_mode_live():
    account = _make_account(trading_mode="live")
    factory = _session_factory(account=account, approval=None, policy=None, cred=None)
    loader = AccountContextLoader(session_factory=factory)
    ctx = await loader.load(str(uuid.uuid4()))

    assert ctx.trading_mode == TradingMode.LIVE


# ── AccountContextLoader.decrypt_credentials ──────────────────────────────────


@pytest.mark.asyncio
async def test_decrypt_credentials_returns_none_when_no_secret_key():
    loader = AccountContextLoader(session_factory=MagicMock(), secret_key="")
    result = await loader.decrypt_credentials(str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_decrypt_credentials_returns_none_for_invalid_uuid():
    loader = AccountContextLoader(session_factory=MagicMock(), secret_key="secret")
    result = await loader.decrypt_credentials("not-a-uuid")
    assert result is None


@pytest.mark.asyncio
async def test_decrypt_credentials_returns_none_when_no_cred_ref():
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result_mock)
    ctx_manager = MagicMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=session)
    ctx_manager.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=ctx_manager)

    loader = AccountContextLoader(session_factory=factory, secret_key="mysecret")
    result = await loader.decrypt_credentials(str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_decrypt_credentials_roundtrip():
    secret_key = "test-secret-key-for-phase7"
    plaintext_key = "my-api-key-12345"
    plaintext_secret = "my-api-secret-67890"

    ct_key, iv_key = encrypt_credential(plaintext_key, secret_key)
    ct_secret, iv_secret = encrypt_credential(plaintext_secret, secret_key)

    # ApiCredentialRef stores single iv; in practice same IV per credential ref.
    # For this test we verify the roundtrip via the decrypt function directly.
    decrypted_key = decrypt_credential(ct_key, iv_key, secret_key)
    decrypted_secret = decrypt_credential(ct_secret, iv_secret, secret_key)

    assert decrypted_key == plaintext_key
    assert decrypted_secret == plaintext_secret


def test_decrypt_credential_wrong_key_raises():
    ct, iv = encrypt_credential("secret", "key-A")
    with pytest.raises(ValueError, match="decryption failed"):
        decrypt_credential(ct, iv, "key-B")


# ── AccountStateReader ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_reader_returns_none_when_no_redis_data():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    reader = AccountStateReader(redis)
    count = await reader.get_open_position_count("user-1")
    assert count is None


@pytest.mark.asyncio
async def test_state_reader_position_count_zero_when_all_flat():
    redis = AsyncMock()
    positions_data = {
        "positions": [
            {"symbol": "BTCUSDT", "position_amt": "0"},
            {"symbol": "ETHUSDT", "position_amt": "0.0"},
        ]
    }
    redis.get = AsyncMock(return_value=json.dumps(positions_data))
    reader = AccountStateReader(redis)
    count = await reader.get_open_position_count("user-1")
    assert count == 0


@pytest.mark.asyncio
async def test_state_reader_position_count_two_open():
    redis = AsyncMock()
    positions_data = {
        "positions": [
            {"symbol": "BTCUSDT", "position_amt": "0.5"},
            {"symbol": "ETHUSDT", "position_amt": "-1.0"},
            {"symbol": "SOLUSDT", "position_amt": "0"},
        ]
    }
    redis.get = AsyncMock(return_value=json.dumps(positions_data))
    reader = AccountStateReader(redis)
    count = await reader.get_open_position_count("user-1")
    assert count == 2


@pytest.mark.asyncio
async def test_state_reader_position_count_empty_positions_list():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps({"positions": []}))
    reader = AccountStateReader(redis)
    count = await reader.get_open_position_count("user-1")
    assert count == 0


@pytest.mark.asyncio
async def test_state_reader_pnl_returns_none_when_no_data():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    reader = AccountStateReader(redis)
    pnl = await reader.get_accumulated_realized_pnl("user-1")
    assert pnl is None


@pytest.mark.asyncio
async def test_state_reader_pnl_sums_accumulated_realized():
    redis = AsyncMock()
    positions_data = {
        "positions": [
            {"symbol": "BTCUSDT", "position_amt": "0.5", "accumulated_realized": "120.50"},
            {"symbol": "ETHUSDT", "position_amt": "-1.0", "accumulated_realized": "-30.00"},
        ]
    }
    redis.get = AsyncMock(return_value=json.dumps(positions_data))
    reader = AccountStateReader(redis)
    pnl = await reader.get_accumulated_realized_pnl("user-1")
    assert pnl == Decimal("90.50")


@pytest.mark.asyncio
async def test_state_reader_pnl_zero_when_no_accumulated_field():
    redis = AsyncMock()
    positions_data = {
        "positions": [
            {"symbol": "BTCUSDT", "position_amt": "0.5"},
        ]
    }
    redis.get = AsyncMock(return_value=json.dumps(positions_data))
    reader = AccountStateReader(redis)
    pnl = await reader.get_accumulated_realized_pnl("user-1")
    assert pnl == Decimal("0")


@pytest.mark.asyncio
async def test_state_reader_uses_correct_redis_key():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    reader = AccountStateReader(redis)
    await reader.get_open_position_count("user-abc")
    # Verify it uses the account_positions key builder
    call_arg = redis.get.call_args[0][0]
    assert "account" in call_arg and "user-abc" in call_arg and "positions" in call_arg


# ── AccountContext frozen dataclass ───────────────────────────────────────────


def test_account_context_is_frozen():
    ctx = AccountContext(
        account_id="acct-1",
        user_id="user-1",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
        has_credentials=False,
        paper_only=True,
    )
    with pytest.raises((AttributeError, TypeError)):
        ctx.paper_only = False  # type: ignore[misc]


def test_account_context_defaults():
    ctx = AccountContext(
        account_id="acct-1",
        user_id="user-1",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
        has_credentials=False,
        paper_only=True,
    )
    assert ctx.allowed_symbols is None
    assert ctx.denied_symbols == ()
    assert isinstance(ctx.limits, RiskLimits)
