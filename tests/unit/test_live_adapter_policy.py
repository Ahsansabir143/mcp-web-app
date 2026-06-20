"""Track D tests — LiveTradingPolicy and LiveExecutionAdapter gating."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.adapter.live_policy import LiveTradingPolicy, PolicyResult
from shared.schemas.enums import ApprovalLevel, MarketType, OrderSide, OrderType, TradingMode
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent


# ── LiveTradingPolicy ─────────────────────────────────────────────────────────


def _policy(
    enabled=False,
    accounts=None,
    symbols=None,
    max_notional=100.0,
) -> LiveTradingPolicy:
    return LiveTradingPolicy(
        live_trading_enabled=enabled,
        account_allowlist=accounts,
        symbol_allowlist=symbols,
        max_notional_usd=max_notional,
    )


def test_policy_blocked_when_disabled():
    p = _policy(enabled=False, accounts=["acct-1"], symbols=["BTCUSDT"])
    r = p.evaluate("acct-1", "BTCUSDT", Decimal("50"))
    assert r.allowed is False
    assert any("live_trading_disabled" in reason for reason in r.blocked_reasons)


def test_policy_blocked_when_account_not_in_allowlist():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"])
    r = p.evaluate("acct-OTHER", "BTCUSDT", Decimal("50"))
    assert r.allowed is False
    assert any("account_not_allowed" in reason for reason in r.blocked_reasons)


def test_policy_blocked_when_account_allowlist_empty():
    p = _policy(enabled=True, accounts=[], symbols=["BTCUSDT"])
    r = p.evaluate("acct-1", "BTCUSDT", Decimal("50"))
    assert r.allowed is False
    assert any("account_not_allowed" in reason for reason in r.blocked_reasons)


def test_policy_blocked_when_symbol_not_in_allowlist():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["ETHUSDT"])
    r = p.evaluate("acct-1", "BTCUSDT", Decimal("50"))
    assert r.allowed is False
    assert any("symbol_not_allowed" in reason for reason in r.blocked_reasons)


def test_policy_blocked_when_notional_exceeds_cap():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"], max_notional=100.0)
    r = p.evaluate("acct-1", "BTCUSDT", Decimal("101"))
    assert r.allowed is False
    assert any("notional_exceeds_cap" in reason for reason in r.blocked_reasons)


def test_policy_allowed_when_all_gates_pass():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"], max_notional=500.0)
    r = p.evaluate("acct-1", "BTCUSDT", Decimal("100"))
    assert r.allowed is True
    assert r.blocked_reasons == []


def test_policy_allowed_without_notional_check():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"])
    r = p.evaluate("acct-1", "BTCUSDT", notional_usd=None)
    assert r.allowed is True


def test_policy_dry_run_returns_not_allowed_even_when_gates_pass():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"])
    r = p.evaluate("acct-1", "BTCUSDT", dry_run=True)
    assert r.allowed is False
    assert r.is_dry_run is True
    assert r.blocked_reasons == []  # gates passed, but dry_run prevents execution


def test_policy_multiple_failures_all_reported():
    p = _policy(enabled=False, accounts=[], symbols=[])
    r = p.evaluate("acct-x", "UNKNWN", Decimal("999"))
    assert len(r.blocked_reasons) >= 3


def test_policy_symbol_case_insensitive():
    p = _policy(enabled=True, accounts=["acct-1"], symbols=["btcusdt"])
    r = p.evaluate("acct-1", "BTCUSDT")
    assert r.allowed is True


def test_policy_from_settings():
    settings = MagicMock()
    settings.live_trading_enabled = True
    settings.live_trading_account_allowlist = "acct-1,acct-2"
    settings.live_trading_symbol_allowlist = "BTCUSDT,ETHUSDT"
    settings.live_max_notional_usd = 250.0

    p = LiveTradingPolicy.from_settings(settings)
    r = p.evaluate("acct-1", "ETHUSDT", Decimal("100"))
    assert r.allowed is True


# ── LiveExecutionAdapter ──────────────────────────────────────────────────────


def _intent(**kwargs):
    defaults = dict(
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
        order_type=OrderType.MARKET,
        size_usd=Decimal("65"),
    )
    defaults.update(kwargs)
    return TradeIntent(**defaults)


def _request(intent=None, account_id="acct-1"):
    return ExecutionRequest(
        trade_intent=intent or _intent(),
        user_id="u1",
        account_id=account_id,
        trading_mode=TradingMode.LIVE,
        approval_level=ApprovalLevel.L4_BOUNDED_AUTO,
    )


@pytest.mark.asyncio
async def test_live_adapter_blocked_when_disabled():
    from services.execution.adapter.live import LiveExecutionAdapter

    policy = _policy(enabled=False, accounts=["acct-1"], symbols=["BTCUSDT"])
    adapter = LiveExecutionAdapter(policy=policy)
    resp = await adapter.submit(_request(), "coid-blocked")

    assert resp.success is False
    assert "blocked_by_policy" in (resp.error or "")
    assert "live_trading_disabled" in (resp.error or "")


@pytest.mark.asyncio
async def test_live_adapter_dry_run_returns_preview_when_policy_passes():
    from services.execution.adapter.live import LiveExecutionAdapter

    policy = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"], max_notional=500.0)
    adapter = LiveExecutionAdapter(policy=policy)
    intent = _intent(metadata={"dry_run": True})
    resp = await adapter.submit(_request(intent=intent), "coid-dry")

    assert resp.success is False
    assert "dry_run_preview" in (resp.error or "")
    assert "would_allow" in (resp.error or "")


@pytest.mark.asyncio
async def test_live_adapter_dry_run_blocked_when_policy_fails():
    from services.execution.adapter.live import LiveExecutionAdapter

    policy = _policy(enabled=False)
    adapter = LiveExecutionAdapter(policy=policy)
    intent = _intent(metadata={"dry_run": True})
    resp = await adapter.submit(_request(intent=intent), "coid-dry-blocked")

    assert resp.success is False
    assert "dry_run_preview" in (resp.error or "")
    assert "would_block" in (resp.error or "")


@pytest.mark.asyncio
async def test_live_adapter_logs_incident_when_blocked():
    from services.execution.adapter.live import LiveExecutionAdapter

    policy = _policy(enabled=False)
    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    adapter = LiveExecutionAdapter(policy=policy, incident_logger=incident_logger)
    resp = await adapter.submit(_request(), "coid-incident")

    assert resp.success is False
    incident_logger.log_incident.assert_awaited_once()
    call_kwargs = incident_logger.log_incident.call_args.kwargs
    assert call_kwargs["incident_type"] == "live_trade_blocked_by_policy"


@pytest.mark.asyncio
async def test_live_adapter_adapter_name_is_live():
    from services.execution.adapter.live import LiveExecutionAdapter

    adapter = LiveExecutionAdapter(policy=_policy())
    assert adapter.adapter_name() == "live"


@pytest.mark.asyncio
async def test_live_adapter_submits_when_all_gates_pass():
    from services.execution.adapter.live import LiveExecutionAdapter

    policy = _policy(enabled=True, accounts=["acct-1"], symbols=["BTCUSDT"], max_notional=500.0)
    adapter = LiveExecutionAdapter(
        policy=policy,
        api_key="testkey",
        api_secret="testsecret",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={
        "orderId": 99999,
        "status": "FILLED",
        "executedQty": "0.001",
        "cummulativeQuoteQty": "65.00",
        "fills": [{"price": "65000", "commission": "0.04", "commissionAsset": "USDT"}],
    })

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        resp = await adapter.submit(_request(), "coid-live")

    assert resp.success is True
    assert resp.exchange_order_id == "99999"
