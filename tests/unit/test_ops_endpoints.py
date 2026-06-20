"""Tests for Phase 9: admin/ops endpoints."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.gateway_api.main import app

ADMIN_KEY = "test-admin-key"
GATEWAY_KEY = "test-gateway-key"


def _setup_app():
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.exists = AsyncMock(return_value=0)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    redis.xlen = AsyncMock(return_value=42)
    redis.xinfo_groups = AsyncMock(return_value=[
        {"name": "grp1", "consumers": 1, "pending": 3, "last-delivered-id": "0-0", "lag": 0},
    ])

    sf = MagicMock()
    session = AsyncMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)

    app.state.redis = redis
    app.state.session_factory = sf
    return redis, sf, session


# ── Auth: admin key enforcement ───────────────────────────────────────────────


def test_ops_streams_401_without_key():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            _setup_app()
            resp = client.get("/api/ops/streams")
    assert resp.status_code == 401


def test_ops_streams_401_with_gateway_key():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            _setup_app()
            resp = client.get("/api/ops/streams", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


def test_ops_streams_rejects_regular_gateway_key():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with patch("services.gateway_api.config.settings.gateway_api_key", GATEWAY_KEY):
            with TestClient(app) as client:
                _setup_app()
                # Regular gateway key must NOT work on admin endpoints
                resp = client.get("/api/ops/streams", headers={"X-API-Key": GATEWAY_KEY})
    assert resp.status_code == 401


def test_ops_trading_mode_401_without_key():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            _setup_app()
            resp = client.post("/api/ops/trading-mode", json={"mode": "paper_only"})
    assert resp.status_code == 401


def test_ops_kill_switch_401_without_key():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            _setup_app()
            resp = client.post("/api/ops/kill-switch", json={"action": "activate", "account_id": "x"})
    assert resp.status_code == 401


# ── GET /api/ops/streams ──────────────────────────────────────────────────────


def test_ops_streams_returns_all_six():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.get("/api/ops/streams", headers={"X-API-Key": ADMIN_KEY})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_streams"] == 6
    stream_names = {s["stream"] for s in body["streams"]}
    assert "stream:binance:raw" in stream_names
    assert "stream:strategy:intents" in stream_names
    assert "stream:execution:events" in stream_names


def test_ops_streams_includes_length_and_groups():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.get("/api/ops/streams", headers={"X-API-Key": ADMIN_KEY})

    body = resp.json()
    for stream_entry in body["streams"]:
        assert "length" in stream_entry
        assert "consumer_groups" in stream_entry
        assert stream_entry["length"] == 42  # mocked XLEN return
        if stream_entry["consumer_groups"]:
            grp = stream_entry["consumer_groups"][0]
            assert "name" in grp
            assert "pending" in grp
            assert "lag" in grp


def test_ops_streams_handles_xinfo_error_gracefully():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            redis.xinfo_groups = AsyncMock(side_effect=Exception("no groups"))
            resp = client.get("/api/ops/streams", headers={"X-API-Key": ADMIN_KEY})

    assert resp.status_code == 200
    body = resp.json()
    for s in body["streams"]:
        assert s["consumer_groups"] == []


# ── GET /api/ops/strategy/{id}/status ────────────────────────────────────────


def test_ops_strategy_status_404_missing():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, sf, session = _setup_app()
            session.get = AsyncMock(return_value=None)
            resp = client.get(
                f"/api/ops/strategy/{uuid.uuid4()}/status",
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 404


def test_ops_strategy_status_422_bad_uuid():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            _setup_app()
            resp = client.get(
                "/api/ops/strategy/not-a-uuid/status",
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 422


def test_ops_strategy_status_ok():
    sid = uuid.uuid4()
    strategy = MagicMock()
    strategy.id = sid
    strategy.name = "test-strat"
    strategy.state = "paper_active"
    strategy.current_version = 1
    strategy.market_type = "futures"
    strategy.symbol_filters = ["BTCUSDT"]
    strategy.created_at = None
    strategy.updated_at = None

    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, sf, session = _setup_app()
            session.get = AsyncMock(return_value=strategy)
            # For the select() queries, return empty scalars
            exec_result = AsyncMock()
            exec_result.scalar_one_or_none = MagicMock(return_value=None)
            session.execute = AsyncMock(return_value=exec_result)
            resp = client.get(
                f"/api/ops/strategy/{sid}/status",
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "paper_active"
    assert body["is_emitting_intents"] is True
    assert body["name"] == "test-strat"


def test_ops_strategy_status_non_emitting():
    sid = uuid.uuid4()
    strategy = MagicMock()
    strategy.id = sid
    strategy.name = "draft-strat"
    strategy.state = "draft"
    strategy.current_version = 1
    strategy.market_type = "futures"
    strategy.symbol_filters = []
    strategy.created_at = None
    strategy.updated_at = None

    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, sf, session = _setup_app()
            session.get = AsyncMock(return_value=strategy)
            exec_result = AsyncMock()
            exec_result.scalar_one_or_none = MagicMock(return_value=None)
            session.execute = AsyncMock(return_value=exec_result)
            resp = client.get(
                f"/api/ops/strategy/{sid}/status",
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    assert resp.json()["is_emitting_intents"] is False


# ── GET /api/ops/execution/jobs ───────────────────────────────────────────────


def test_ops_jobs_returns_list():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, sf, session = _setup_app()
            exec_result = MagicMock()  # sync MagicMock: .scalars().all() is sync
            exec_result.scalars.return_value.all.return_value = []
            session.execute = AsyncMock(return_value=exec_result)
            resp = client.get("/api/ops/execution/jobs", headers={"X-API-Key": ADMIN_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "jobs" in body
    assert "count" in body
    assert "filters" in body


def test_ops_jobs_filters_included_in_response():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, sf, session = _setup_app()
            exec_result = MagicMock()
            exec_result.scalars.return_value.all.return_value = []
            session.execute = AsyncMock(return_value=exec_result)
            resp = client.get(
                "/api/ops/execution/jobs?status=failed&symbol=BTCUSDT",
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filters"]["status"] == "failed"
    assert body["filters"]["symbol"] == "BTCUSDT"


# ── POST /api/ops/trading-mode ────────────────────────────────────────────────


def test_ops_trading_mode_sets_paper_only():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/trading-mode",
                json={"mode": "paper_only", "reason": "reset"},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "paper_only"
    redis.set.assert_awaited()
    redis.delete.assert_awaited()  # emergency stop key cleared


def test_ops_trading_mode_emergency_stop_sets_flag():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/trading-mode",
                json={"mode": "emergency_stop", "reason": "incident"},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "emergency_stop"
    # Both global:trading_mode AND global:emergency_stop must be set
    set_calls = [str(c) for c in redis.set.call_args_list]
    assert any("emergency_stop" in c for c in set_calls)


def test_ops_trading_mode_mixed_accepted():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/trading-mode",
                json={"mode": "mixed"},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "mixed"
    assert "not yet implemented" in body["note"]


def test_ops_trading_mode_invalid_422():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/trading-mode",
                json={"mode": "live"},  # not a valid mode
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 422


def test_ops_get_trading_mode():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            redis.get = AsyncMock(return_value="paper_only")
            redis.exists = AsyncMock(return_value=0)
            resp = client.get("/api/ops/trading-mode", headers={"X-API-Key": ADMIN_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "paper_only"
    assert body["emergency_stop_active"] is False


# ── POST /api/ops/kill-switch ─────────────────────────────────────────────────


def test_ops_kill_switch_activate():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/kill-switch",
                json={"action": "activate", "account_id": "acc-1", "ttl_s": 3600},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "activate"
    assert body["account_id"] == "acc-1"
    assert body["ttl_s"] == 3600


def test_ops_kill_switch_clear():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/kill-switch",
                json={"action": "clear", "account_id": "acc-1"},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    assert resp.json()["action"] == "clear"


def test_ops_kill_switch_pause_symbol():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/kill-switch",
                json={"action": "pause_symbol", "account_id": "acc-1", "symbol": "ETHUSDT"},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "ETHUSDT"


def test_ops_kill_switch_pause_symbol_requires_symbol():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/kill-switch",
                json={"action": "pause_symbol", "account_id": "acc-1"},  # missing symbol
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 422


def test_ops_kill_switch_invalid_action():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            resp = client.post(
                "/api/ops/kill-switch",
                json={"action": "nuke", "account_id": "acc-1"},
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 422


def test_ops_kill_switch_status():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, _, _ = _setup_app()
            redis.exists = AsyncMock(return_value=1)
            resp = client.get(
                "/api/ops/kill-switch/acc-1",
                headers={"X-API-Key": ADMIN_KEY},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert "kill_switch_active" in body
    assert "user_paused" in body
    assert "circuit_breaker_active" in body


# ── GET /api/ops/metrics ──────────────────────────────────────────────────────


def test_ops_metrics_shape():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            redis, sf, session = _setup_app()
            redis.get = AsyncMock(return_value="paper_only")
            redis.exists = AsyncMock(return_value=0)
            # DB incident query returns empty
            count_result = AsyncMock()
            count_result.__iter__ = MagicMock(return_value=iter([]))
            scalar_result = AsyncMock()
            scalar_result.scalar_one = MagicMock(return_value=0)
            session.execute = AsyncMock(side_effect=[count_result, scalar_result])
            resp = client.get("/api/ops/metrics", headers={"X-API-Key": ADMIN_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "timestamp" in body
    assert "streams" in body
    assert "safety" in body
    assert "incidents" in body
    assert "gateway_counters" in body
    assert body["safety"]["trading_mode"] == "paper_only"
    assert body["safety"]["emergency_stop_active"] is False


def test_ops_metrics_401_without_key():
    with patch("services.gateway_api.config.settings.admin_api_key", ADMIN_KEY):
        with TestClient(app) as client:
            _setup_app()
            resp = client.get("/api/ops/metrics")
    assert resp.status_code == 401


# ── Shared metrics module ─────────────────────────────────────────────────────


def test_metrics_registry_increment_and_snapshot():
    from shared.metrics import MetricsRegistry
    reg = MetricsRegistry()
    reg.increment("jobs_processed", 5)
    reg.increment("jobs_processed", 3)
    reg.increment("errors")
    snap = reg.snapshot()
    assert snap["counters"]["jobs_processed"] == 8
    assert snap["counters"]["errors"] == 1


def test_metrics_registry_gauge():
    from shared.metrics import MetricsRegistry
    reg = MetricsRegistry()
    reg.set_gauge("active_sessions", 12.0)
    assert reg.get_gauge("active_sessions") == 12.0
    snap = reg.snapshot()
    assert snap["gauges"]["active_sessions"] == 12.0


def test_metrics_registry_get_counter():
    from shared.metrics import MetricsRegistry
    reg = MetricsRegistry()
    assert reg.get_counter("never_set") == 0
    reg.increment("x")
    assert reg.get_counter("x") == 1


# ── StructuredLogger ──────────────────────────────────────────────────────────


def test_structured_logger_accepts_kwargs():
    from shared.utils.logging import get_logger
    log = get_logger("test.ops")
    # Should not raise TypeError
    log.info("test message", job_id="abc-123", symbol="BTCUSDT", strategy_id="xyz")
    log.warning("warn with ctx", error_code=42)
    log.error("error with ctx", exc_info=False)


def test_structured_logger_exception_sets_exc_info():
    import logging
    from shared.utils.logging import StructuredLogger

    captured = []

    class CapturingHandler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    inner = logging.getLogger("test.exception_capture")
    inner.handlers.clear()
    inner.addHandler(CapturingHandler())
    inner.setLevel(logging.DEBUG)

    slog = StructuredLogger(inner)
    try:
        raise ValueError("test error")
    except ValueError:
        slog.exception("something failed", context="test")

    assert len(captured) == 1
    assert captured[0].exc_info is not None
