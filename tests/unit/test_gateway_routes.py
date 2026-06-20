"""Tests for Phase 8: gateway API routes."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.gateway_api.main import app

VALID_KEY = "test-gateway-key"


def _client(api_key: str = VALID_KEY) -> TestClient:
    """Return a TestClient with mocked app state and a patched settings key."""
    client = TestClient(app, raise_server_exceptions=True)
    return client


def _mock_state(client: TestClient):
    """Attach mock redis + session_factory to app.state for the test."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    sf = MagicMock()
    session = AsyncMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    app.state.redis = redis
    app.state.session_factory = sf
    return redis, sf, session


# ── Auth enforcement ──────────────────────────────────────────────────────────


def test_health_no_auth_required():
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200


def test_market_snapshot_401_without_key():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with TestClient(app) as client:
            _mock_state(client)
            resp = client.get("/api/market/snapshot/futures/BTCUSDT")
    assert resp.status_code == 401


def test_market_snapshot_401_wrong_key():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with TestClient(app) as client:
            _mock_state(client)
            resp = client.get(
                "/api/market/snapshot/futures/BTCUSDT",
                headers={"X-API-Key": "wrong"},
            )
    assert resp.status_code == 401


def test_strategies_401_without_key():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with TestClient(app) as client:
            _mock_state(client)
            resp = client.get("/api/strategies")
    assert resp.status_code == 401


def test_paper_trade_401_without_key():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with TestClient(app) as client:
            _mock_state(client)
            resp = client.post("/api/paper-trade", json={})
    assert resp.status_code == 401


# ── Market routes ─────────────────────────────────────────────────────────────


def test_market_snapshot_calls_facade():
    expected = {"symbol": "BTCUSDT", "price": "50000"}
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.market.get_symbol_snapshot",
            new=AsyncMock(return_value=expected),
        ) as mock_facade:
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.get(
                    "/api/market/snapshot/futures/BTCUSDT",
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 200
    assert resp.json()["price"] == "50000"
    mock_facade.assert_awaited_once()


def test_market_snapshot_404_when_none():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.market.get_symbol_snapshot",
            new=AsyncMock(return_value=None),
        ):
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.get(
                    "/api/market/snapshot/futures/NOTOKEN",
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 404


# ── Strategy routes ───────────────────────────────────────────────────────────


def test_list_strategies_calls_facade():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.strategies.list_strategies",
            new=AsyncMock(return_value=[{"id": "abc"}]),
        ) as mock:
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.get(
                    "/api/strategies?limit=10",
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 200
    mock.assert_awaited_once()
    _, kwargs = mock.call_args
    assert kwargs.get("limit") == 10 or mock.call_args[0][3] == 10


def test_get_strategy_404():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.strategies.get_strategy_details",
            new=AsyncMock(return_value=None),
        ):
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.get(
                    f"/api/strategies/{uuid.uuid4()}",
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 404


def test_simulate_strategy_calls_facade():
    sid = str(uuid.uuid4())
    expected = {"signal": "long"}
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.strategies.simulate_strategy_on_snapshot",
            new=AsyncMock(return_value=expected),
        ) as mock:
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.post(
                    f"/api/strategies/{sid}/simulate",
                    json={"symbol": "ETHUSDT"},
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 200
    assert resp.json()["signal"] == "long"
    mock.assert_awaited_once()


def test_update_state_calls_facade():
    sid = str(uuid.uuid4())
    expected = {"success": True, "new_state": "simulation"}
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.strategies.update_strategy_state",
            new=AsyncMock(return_value=expected),
        ) as mock:
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.post(
                    f"/api/strategies/{sid}/state",
                    json={"target_state": "simulation", "justification": "test"},
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock.assert_awaited_once()


# ── Execution routes ──────────────────────────────────────────────────────────


def test_recent_executions_calls_facade():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.executions.get_recent_executions",
            new=AsyncMock(return_value=[{"job_id": "x"}]),
        ) as mock:
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.get(
                    "/api/executions/recent",
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 200
    mock.assert_awaited_once()


# ── Paper trade route ─────────────────────────────────────────────────────────


def test_paper_trade_invalid_side_422():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with TestClient(app) as client:
            _mock_state(client)
            resp = client.post(
                "/api/paper-trade",
                json={
                    "strategy_id": str(uuid.uuid4()),
                    "symbol": "BTCUSDT",
                    "side": "INVALID",
                    "size": 0.1,
                },
                headers={"X-API-Key": VALID_KEY},
            )
    assert resp.status_code == 422


def test_paper_trade_queued():
    intent_id = str(uuid.uuid4())
    expected = {"status": "queued", "intent_id": intent_id, "mode": "paper"}
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.paper_trade.request_paper_trade",
            new=AsyncMock(return_value=expected),
        ) as mock:
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.post(
                    "/api/paper-trade",
                    json={
                        "strategy_id": str(uuid.uuid4()),
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "size_usd": 500,
                    },
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    mock.assert_awaited_once()


def test_paper_trade_strategy_not_found_404():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.paper_trade.request_paper_trade",
            new=AsyncMock(return_value={"error": "strategy_not_found", "message": "not found"}),
        ):
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.post(
                    "/api/paper-trade",
                    json={
                        "strategy_id": str(uuid.uuid4()),
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "size": 0.1,
                    },
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 404


def test_paper_trade_inactive_strategy_400():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.routes.paper_trade.request_paper_trade",
            new=AsyncMock(
                return_value={"error": "strategy_not_active", "message": "wrong state"}
            ),
        ):
            with TestClient(app) as client:
                _mock_state(client)
                resp = client.post(
                    "/api/paper-trade",
                    json={
                        "strategy_id": str(uuid.uuid4()),
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "size": 0.1,
                    },
                    headers={"X-API-Key": VALID_KEY},
                )
    assert resp.status_code == 400


# ── Rate limiting ─────────────────────────────────────────────────────────────


def test_rate_limit_429_when_exceeded():
    with patch(
        "services.gateway_api.config.settings.gateway_api_key", VALID_KEY
    ):
        with patch(
            "services.gateway_api.config.settings.rate_limit_requests_per_min", 1
        ):
            with patch(
                "services.gateway_api.routes.market.get_symbol_snapshot",
                new=AsyncMock(return_value={"price": "1"}),
            ):
                with TestClient(app) as client:
                    redis, _, _ = _mock_state(client)
                    # Second call returns count=2 which exceeds limit=1
                    redis.incr = AsyncMock(side_effect=[1, 2])
                    resp1 = client.get(
                        "/api/market/snapshot/futures/BTCUSDT",
                        headers={"X-API-Key": VALID_KEY},
                    )
                    resp2 = client.get(
                        "/api/market/snapshot/futures/BTCUSDT",
                        headers={"X-API-Key": VALID_KEY},
                    )
    assert resp1.status_code == 200
    assert resp2.status_code == 429


# ── Health endpoints ──────────────────────────────────────────────────────────


def test_health_detail_ok():
    with TestClient(app) as client:
        redis, sf, session = _mock_state(client)
        session.execute = AsyncMock(return_value=MagicMock())
        resp = client.get("/api/health/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "gateway-api"
    assert "dependencies" in body


def test_health_detail_degraded_when_redis_down():
    with TestClient(app) as client:
        redis, sf, session = _mock_state(client)
        redis.ping = AsyncMock(side_effect=Exception("connection refused"))
        resp = client.get("/api/health/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["redis"] == "error"
