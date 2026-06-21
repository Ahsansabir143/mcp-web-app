"""Tests for Phase 8: MCP tool handlers and protocol helpers."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.mcp_server import protocol as proto
from services.mcp_server.tools import control as control_tools
from services.mcp_server.tools import read as read_tools
from services.mcp_server.tools import simulation as sim_tools


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_redis(snapshot=None, price=None):
    r = AsyncMock()
    r.get = AsyncMock(return_value=snapshot or price)
    r.ping = AsyncMock(return_value=True)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    return r


def _sf():
    """Return a MagicMock that is also an async context manager (session_factory)."""
    sf = MagicMock()
    session = AsyncMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    return sf, session


# ── Protocol helpers ──────────────────────────────────────────────────────────


def test_protocol_ok_serialises():
    result = proto.ok(1, {"a": "b"})
    obj = json.loads(result)
    assert obj["jsonrpc"] == "2.0"
    assert obj["id"] == 1
    assert obj["result"] == {"a": "b"}


def test_protocol_error_serialises():
    result = proto.error(None, -32600, "bad request")
    obj = json.loads(result)
    assert obj["error"]["code"] == -32600
    assert "bad request" in obj["error"]["message"]


def test_tool_content_wraps_dict():
    content = proto.tool_content({"x": 1})
    assert content["isError"] is False
    assert content["content"][0]["type"] == "text"
    inner = json.loads(content["content"][0]["text"])
    assert inner["x"] == 1


def test_tool_error_sets_flag():
    err = proto.tool_error("something went wrong")
    assert err["isError"] is True
    assert "something went wrong" in err["content"][0]["text"]


def test_initialize_result_fields():
    res = proto.initialize_result("my-server", "0.1.0")
    assert res["protocolVersion"] == "2024-11-05"
    assert res["serverInfo"]["name"] == "my-server"
    assert "tools" in res["capabilities"]


def test_tools_list_has_all_tools():
    tl = proto.tools_list_result()
    names = {t["name"] for t in tl["tools"]}
    expected = {
        "get_symbol_snapshot",
        "list_strategies",
        "get_strategy_details",
        "get_recent_executions",
        "get_incidents",
        "simulate_strategy_on_snapshot",
        "simulate_strategy_on_range",
        "request_paper_trade",
        "update_strategy_state",
        # Phase 2 — live account observability
        "get_account_connection_status",
        "get_account_balances",
        "get_account_positions",
        "get_open_orders",
        "get_recent_fills",
        "check_live_trade_policy",
        "get_stream_health",
    }
    assert expected == names


# ── Read tools ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_symbol_snapshot_missing_symbol():
    result = await read_tools.get_symbol_snapshot(
        {}, redis=AsyncMock(), session_factory=MagicMock()
    )
    assert result.get("error") == "missing_argument"


@pytest.mark.asyncio
async def test_get_symbol_snapshot_calls_facade():
    redis = _make_redis(snapshot=json.dumps({"price": "50000"}))

    with patch(
        "services.mcp_server.tools.read.market_facade.get_symbol_snapshot",
        new=AsyncMock(return_value={"price": "50000"}),
    ) as mock_facade:
        result = await read_tools.get_symbol_snapshot(
            {"symbol": "BTCUSDT", "market_type": "futures"},
            redis=redis,
            session_factory=MagicMock(),
        )
    mock_facade.assert_awaited_once_with(redis, "futures", "BTCUSDT")
    assert result["price"] == "50000"


@pytest.mark.asyncio
async def test_list_strategies_wraps_result():
    with patch(
        "services.mcp_server.tools.read.strat_facade.list_strategies",
        new=AsyncMock(return_value=[{"id": "abc", "name": "test"}]),
    ):
        result = await read_tools.list_strategies(
            {"limit": "5"}, redis=AsyncMock(), session_factory=MagicMock()
        )
    assert result["count"] == 1
    assert result["strategies"][0]["name"] == "test"


@pytest.mark.asyncio
async def test_get_strategy_details_not_found():
    with patch(
        "services.mcp_server.tools.read.strat_facade.get_strategy_details",
        new=AsyncMock(return_value=None),
    ):
        result = await read_tools.get_strategy_details(
            {"strategy_id": str(uuid.uuid4())},
            redis=AsyncMock(),
            session_factory=MagicMock(),
        )
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_get_recent_executions_passes_args():
    sid = str(uuid.uuid4())
    with patch(
        "services.mcp_server.tools.read.exec_facade.get_recent_executions",
        new=AsyncMock(return_value=[]),
    ) as mock:
        await read_tools.get_recent_executions(
            {"strategy_id": sid, "symbol": "BTCUSDT", "limit": "10"},
            redis=AsyncMock(),
            session_factory=MagicMock(),
        )
    mock.assert_awaited_once()
    _, kwargs = mock.call_args
    assert kwargs["strategy_id"] == sid
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["limit"] == 10


@pytest.mark.asyncio
async def test_get_incidents_passes_since_ts():
    with patch(
        "services.mcp_server.tools.read.exec_facade.get_incidents",
        new=AsyncMock(return_value=[]),
    ) as mock:
        await read_tools.get_incidents(
            {"since_ts": "1700000000000"},
            redis=AsyncMock(),
            session_factory=MagicMock(),
        )
    _, kwargs = mock.call_args
    assert kwargs["since_ts"] == 1700000000000


# ── Simulation tools ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_simulate_on_snapshot_missing_symbol():
    result = await sim_tools.simulate_strategy_on_snapshot(
        {"strategy_id": str(uuid.uuid4())},
        redis=AsyncMock(),
        session_factory=MagicMock(),
    )
    assert result.get("error") == "missing_argument"


@pytest.mark.asyncio
async def test_simulate_on_snapshot_delegates():
    sid = str(uuid.uuid4())
    expected = {"signal": "long", "confidence": 0.8}
    with patch(
        "services.mcp_server.tools.simulation.strat_facade.simulate_strategy_on_snapshot",
        new=AsyncMock(return_value=expected),
    ) as mock:
        result = await sim_tools.simulate_strategy_on_snapshot(
            {"strategy_id": sid, "symbol": "BTCUSDT", "market_type": "spot"},
            redis=AsyncMock(),
            session_factory=MagicMock(),
        )
    mock.assert_awaited_once()
    assert result["signal"] == "long"


@pytest.mark.asyncio
async def test_simulate_on_range_returns_stub():
    result = await sim_tools.simulate_strategy_on_range(
        {"strategy_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        redis=AsyncMock(),
        session_factory=MagicMock(),
    )
    assert result["status"] == "not_implemented"
    assert "simulate_strategy_on_snapshot" in result.get("available_tool", "")


# ── Control tools ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_paper_trade_missing_args():
    for missing_field, args in [
        ("strategy_id", {"symbol": "BTCUSDT", "side": "BUY"}),
        ("symbol", {"strategy_id": str(uuid.uuid4()), "side": "BUY"}),
        ("side", {"strategy_id": str(uuid.uuid4()), "symbol": "BTCUSDT"}),
    ]:
        result = await control_tools.request_paper_trade(
            args, redis=AsyncMock(), session_factory=MagicMock()
        )
        assert result.get("error") == "missing_argument", f"expected error for missing {missing_field}"


@pytest.mark.asyncio
async def test_request_paper_trade_delegates():
    sid = str(uuid.uuid4())
    expected = {"status": "queued", "intent_id": str(uuid.uuid4())}
    with patch(
        "services.mcp_server.tools.control.exec_facade.request_paper_trade",
        new=AsyncMock(return_value=expected),
    ) as mock:
        result = await control_tools.request_paper_trade(
            {
                "strategy_id": sid,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "size_usd": 1000,
                "reason": "test",
            },
            redis=AsyncMock(),
            session_factory=MagicMock(),
        )
    assert result["status"] == "queued"
    _, kwargs = mock.call_args
    assert kwargs["side"] == "BUY"
    assert kwargs["size_usd"] == 1000.0


@pytest.mark.asyncio
async def test_update_strategy_state_missing_justification():
    result = await control_tools.update_strategy_state(
        {"strategy_id": str(uuid.uuid4()), "target_state": "simulation"},
        redis=AsyncMock(),
        session_factory=MagicMock(),
    )
    assert result.get("error") == "missing_argument"
    assert "justification" in result.get("message", "")


@pytest.mark.asyncio
async def test_update_strategy_state_delegates():
    sid = str(uuid.uuid4())
    expected = {"success": True, "new_state": "simulation"}
    with patch(
        "services.mcp_server.tools.control.strat_facade.update_strategy_state",
        new=AsyncMock(return_value=expected),
    ) as mock:
        result = await control_tools.update_strategy_state(
            {
                "strategy_id": sid,
                "target_state": "simulation",
                "justification": "moving to sim",
                "approval_level": "l2_paper",
            },
            redis=AsyncMock(),
            session_factory=MagicMock(),
        )
    assert result["success"] is True
    _, kwargs = mock.call_args
    assert kwargs["target_state"] == "simulation"
    assert kwargs["justification"] == "moving to sim"
    assert kwargs["user_approval_level"] == "l2_paper"


# ── Safety properties ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_paper_trade_does_not_bypass_execution_facade():
    """Verify paper trade goes through exec_facade, not directly to Redis stream."""
    with patch(
        "services.mcp_server.tools.control.exec_facade.request_paper_trade",
        new=AsyncMock(return_value={"status": "queued"}),
    ) as mock_facade:
        with patch(
            "shared.redis.client.stream_publish",
            new=AsyncMock(),
        ) as mock_stream:
            await control_tools.request_paper_trade(
                {
                    "strategy_id": str(uuid.uuid4()),
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "size": 1.5,
                },
                redis=AsyncMock(),
                session_factory=MagicMock(),
            )

    # The tool must call the facade (which handles validation + risk)
    mock_facade.assert_awaited_once()
    # The tool must NOT bypass the facade and write directly to the stream
    mock_stream.assert_not_called()


@pytest.mark.asyncio
async def test_simulate_does_not_publish_to_stream():
    """Simulation tool must never publish a TradeIntent."""
    with patch(
        "services.mcp_server.tools.simulation.strat_facade.simulate_strategy_on_snapshot",
        new=AsyncMock(return_value={"signal": "none"}),
    ):
        with patch(
            "shared.redis.client.stream_publish",
            new=AsyncMock(),
        ) as mock_stream:
            await sim_tools.simulate_strategy_on_snapshot(
                {"strategy_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
                redis=AsyncMock(),
                session_factory=MagicMock(),
            )

    mock_stream.assert_not_called()
