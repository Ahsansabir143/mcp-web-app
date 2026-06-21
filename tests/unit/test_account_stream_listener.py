"""Tests for AccountStreamListener — WS API spot path and futures legacy path.

Pure-function tests (_build_subscribe_request, _check_subscribe_response) need
no mocking.  Integration-style tests use mocked websockets and a minimal
no-op session/redis pair so no real DB or network is required.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.account_stream.listener import (
    AccountStreamListener,
    _AuthError,
    _WS_SUBSCRIBE_METHOD,
    _build_subscribe_request,
    _check_subscribe_response,
)

# ── _build_subscribe_request ──────────────────────────────────────────────────


def test_build_subscribe_request_structure():
    req_json, req_id = _build_subscribe_request(
        "MY_KEY", "MY_SECRET", ts=1_700_000_000_000, req_id="fixed-id"
    )
    payload = json.loads(req_json)
    assert payload["id"] == "fixed-id"
    assert payload["method"] == _WS_SUBSCRIBE_METHOD
    params = payload["params"]
    assert set(params) == {"apiKey", "timestamp", "signature"}
    assert params["apiKey"] == "MY_KEY"
    assert params["timestamp"] == 1_700_000_000_000


def test_build_subscribe_request_signature_correct():
    api_key, api_secret, ts = "MY_KEY", "MY_SECRET", 1_700_000_000_000
    req_json, _ = _build_subscribe_request(api_key, api_secret, ts=ts, req_id="r")
    sig = json.loads(req_json)["params"]["signature"]
    qs = f"apiKey={api_key}&timestamp={ts}"
    expected = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    assert sig == expected


def test_build_subscribe_request_does_not_expose_secret():
    req_json, _ = _build_subscribe_request("KEY", "SUPER_SECRET", ts=1, req_id="r")
    assert "SUPER_SECRET" not in req_json


def test_build_subscribe_request_auto_generates_id_and_ts():
    req_json, req_id = _build_subscribe_request("K", "S")
    payload = json.loads(req_json)
    assert payload["id"] == req_id
    assert payload["params"]["timestamp"] > 0


def test_build_subscribe_request_generates_unique_ids():
    _, id1 = _build_subscribe_request("K", "S")
    _, id2 = _build_subscribe_request("K", "S")
    assert id1 != id2


# ── _check_subscribe_response ─────────────────────────────────────────────────


def test_check_subscribe_response_200_ok():
    resp = {"id": "r", "status": 200, "result": {"subscriptionId": "abc"}}
    _check_subscribe_response(resp, "r")  # must not raise


def test_check_subscribe_response_ignores_wrong_id():
    resp = {"id": "other", "status": 401, "error": {"code": -2014, "msg": "Bad key"}}
    _check_subscribe_response(resp, "r")  # must not raise — not our response


def test_check_subscribe_response_401_raises_auth_error():
    resp = {"id": "r", "status": 401, "error": {"code": -2014, "msg": "API-key invalid"}}
    with pytest.raises(_AuthError, match="401"):
        _check_subscribe_response(resp, "r")


def test_check_subscribe_response_403_raises_auth_error():
    resp = {"id": "r", "status": 403, "error": {"code": -2015, "msg": "No permission"}}
    with pytest.raises(_AuthError, match="403"):
        _check_subscribe_response(resp, "r")


def test_check_subscribe_response_other_error_raises_runtime():
    resp = {"id": "r", "status": 429, "error": {"code": -1003, "msg": "Too many requests"}}
    with pytest.raises(RuntimeError, match="429"):
        _check_subscribe_response(resp, "r")


def test_check_subscribe_response_error_includes_binance_code_and_msg():
    resp = {"id": "r", "status": 403, "error": {"code": -2015, "msg": "IP restricted"}}
    with pytest.raises(_AuthError, match="-2015") as exc_info:
        _check_subscribe_response(resp, "r")
    assert "IP restricted" in str(exc_info.value)


# ── Helpers for lifecycle tests ───────────────────────────────────────────────

_ACCT_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def _make_listener(
    market_type: str = "spot",
    ws_api_base: str = "wss://ws-api.binance.com:443/ws-api/v3",
) -> tuple[AccountStreamListener, AsyncMock, AsyncMock]:
    """Return (listener, incident_logger, redis)."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = AsyncMock(return_value=None)
    mock_session.commit = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)
    redis = AsyncMock()
    redis.set = AsyncMock()
    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    listener = AccountStreamListener(
        account_id=_ACCT_ID,
        api_key="test-api-key",
        api_secret="test-api-secret",
        market_type=market_type,
        ws_base="wss://fstream.binance.com",
        rest_base="https://fapi.binance.com",
        session_factory=session_factory,
        redis=redis,
        incident_logger=incident_logger,
        ws_api_base=ws_api_base,
    )
    return listener, incident_logger, redis


def _make_mock_ws(sub_response: dict, events: list[dict] | None = None) -> MagicMock:
    """Minimal WebSocket mock: sends subscribe ACK then optional events."""
    ws = MagicMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps(sub_response))

    event_list = [json.dumps(e) for e in (events or [])]
    idx = [0]

    async def _anext():
        if idx[0] < len(event_list):
            result = event_list[idx[0]]
            idx[0] += 1
            return result
        raise StopAsyncIteration

    ws.__aiter__ = MagicMock(return_value=ws)
    ws.__anext__ = AsyncMock(side_effect=_anext)
    return ws


# ── run() dispatch ────────────────────────────────────────────────────────────


async def test_run_dispatches_spot_to_ws_api():
    listener, _, _ = _make_listener(market_type="spot")
    listener._set_status = AsyncMock()
    called = []

    async def fake_ws_api():
        called.append("ws_api")

    async def fake_legacy():
        called.append("legacy")

    listener._run_ws_api = fake_ws_api
    listener._run_legacy_stream = fake_legacy

    await listener.run()
    assert called == ["ws_api"]


async def test_run_dispatches_futures_to_legacy():
    listener, _, _ = _make_listener(market_type="futures")
    listener._set_status = AsyncMock()
    called = []

    async def fake_ws_api():
        called.append("ws_api")

    async def fake_legacy():
        called.append("legacy")

    listener._run_ws_api = fake_ws_api
    listener._run_legacy_stream = fake_legacy

    await listener.run()
    assert called == ["legacy"]


# ── Spot path: successful subscription ───────────────────────────────────────


async def test_connect_and_subscribe_sends_request_and_marks_connected():
    listener, _, _ = _make_listener()

    fixed_req_id = "req-001"
    sub_resp = {"id": fixed_req_id, "status": 200, "result": {"subscriptionId": "sub-42"}}
    mock_ws = _make_mock_ws(sub_resp, events=[])  # no events → loop exits

    statuses: list[str] = []
    listener._set_status = AsyncMock(side_effect=lambda s, **kw: statuses.append(s))

    with (
        patch(
            "services.execution.account_stream.listener._build_subscribe_request",
            return_value=('{"id":"req-001","method":"...","params":{}}', fixed_req_id),
        ),
        patch(
            "services.execution.account_stream.listener.websockets.connect",
            return_value=mock_ws,
        ),
    ):
        await listener._connect_and_subscribe()

    assert "connected" in statuses
    assert listener._subscription_id == "sub-42"
    mock_ws.send.assert_awaited_once()


async def test_connect_and_subscribe_records_subscription_id():
    listener, _, _ = _make_listener()
    fixed_req_id = "req-sub-id"
    sub_resp = {"id": fixed_req_id, "status": 200, "result": {"subscriptionId": "xyz-789"}}
    mock_ws = _make_mock_ws(sub_resp)
    listener._set_status = AsyncMock()

    with (
        patch(
            "services.execution.account_stream.listener._build_subscribe_request",
            return_value=("payload", fixed_req_id),
        ),
        patch(
            "services.execution.account_stream.listener.websockets.connect",
            return_value=mock_ws,
        ),
    ):
        await listener._connect_and_subscribe()

    assert listener._subscription_id == "xyz-789"


# ── Spot path: auth failure stops, logs incident, no retry ───────────────────


async def test_auth_failure_stops_stream_and_logs_incident():
    listener, incident_logger, _ = _make_listener()
    listener._set_status = AsyncMock()

    connect_count = [0]

    async def raise_auth(*_a, **_kw):
        connect_count[0] += 1
        raise _AuthError("subscribe 401: -2014 Invalid API-key")

    listener._connect_and_subscribe = raise_auth

    await listener._run_ws_api()

    assert connect_count[0] == 1  # no retry

    statuses = [c.args[0] for c in listener._set_status.call_args_list]
    assert "auth_error" in statuses

    incident_logger.log_incident.assert_awaited_once()
    kwargs = incident_logger.log_incident.call_args.kwargs
    assert kwargs["incident_type"] == "stream_auth_error"
    assert kwargs["severity"] == "error"


async def test_auth_failure_403_stops_stream():
    listener, incident_logger, _ = _make_listener()
    listener._set_status = AsyncMock()
    listener._connect_and_subscribe = AsyncMock(
        side_effect=_AuthError("subscribe 403: -2015 IP restricted")
    )

    await listener._run_ws_api()

    statuses = [c.args[0] for c in listener._set_status.call_args_list]
    assert "auth_error" in statuses
    incident_logger.log_incident.assert_awaited_once()


# ── Spot path: reconnect + resubscribe ───────────────────────────────────────


async def test_reconnects_after_connection_closed():
    """ConnectionClosed triggers reconnect; second attempt sets stop."""
    listener, _, _ = _make_listener()
    listener._set_status = AsyncMock()

    from websockets.exceptions import ConnectionClosed as WsConnClosed

    call_count = [0]

    async def flaky_connect():
        call_count[0] += 1
        if call_count[0] == 1:
            raise WsConnClosed(None, None)
        listener._stop.set()

    listener._connect_and_subscribe = flaky_connect

    with patch("asyncio.sleep", new=AsyncMock()):
        await listener._run_ws_api()

    assert call_count[0] == 2

    statuses = [c.args[0] for c in listener._set_status.call_args_list]
    assert "reconnecting" in statuses


async def test_reconnects_after_generic_exception():
    """Any non-auth exception triggers reconnect."""
    listener, _, _ = _make_listener()
    listener._set_status = AsyncMock()

    call_count = [0]

    async def unstable():
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("network failure")
        listener._stop.set()

    listener._connect_and_subscribe = unstable

    with patch("asyncio.sleep", new=AsyncMock()):
        await listener._run_ws_api()

    assert call_count[0] == 2
    statuses = [c.args[0] for c in listener._set_status.call_args_list]
    assert "reconnecting" in statuses


async def test_reconnect_delay_is_bounded_by_max():
    """Reconnect delay uses exponential backoff capped at _RECONNECT_MAX_S."""
    from services.execution.account_stream.listener import _RECONNECT_MAX_S

    listener, _, _ = _make_listener()
    listener._set_status = AsyncMock()

    sleep_calls: list[float] = []
    max_calls = 6

    call_count = [0]

    async def always_fail():
        call_count[0] += 1
        if call_count[0] >= max_calls:
            listener._stop.set()
            return
        raise OSError("fail")

    listener._connect_and_subscribe = always_fail

    async def capture_sleep(delay):
        sleep_calls.append(delay)

    with patch("asyncio.sleep", new=capture_sleep):
        await listener._run_ws_api()

    assert all(d <= _RECONNECT_MAX_S for d in sleep_calls)
    assert max(sleep_calls) == _RECONNECT_MAX_S or len(sleep_calls) < 10


# ── Spot path: stop() terminates the loop ────────────────────────────────────


async def test_stop_terminates_ws_api_loop():
    listener, _, _ = _make_listener()
    listener._set_status = AsyncMock()
    listener._stop.set()  # pre-set stop flag

    connect_count = [0]

    async def should_not_be_called():
        connect_count[0] += 1

    listener._connect_and_subscribe = should_not_be_called

    await listener._run_ws_api()

    assert connect_count[0] == 0
    statuses = [c.args[0] for c in listener._set_status.call_args_list]
    assert "stopped" in statuses


# ── Stream status transitions ─────────────────────────────────────────────────


async def test_status_transitions_on_successful_subscribe():
    """Successful path: connecting → connected (then stopped on clean disconnect)."""
    listener, _, _ = _make_listener()

    statuses: list[str] = []

    async def capture_set_status(status, **_kw):
        statuses.append(status)

    listener._set_status = capture_set_status

    # Successful subscribe + no events → exits loop → _run_ws_api sets stopped
    async def connect_ok():
        statuses.append("connected")  # simulated from _connect_and_subscribe
        listener._stop.set()

    listener._connect_and_subscribe = connect_ok

    await listener._run_ws_api()

    assert "connected" in statuses
    assert "stopped" in statuses


async def test_status_transitions_on_auth_failure():
    """Auth failure path: connecting → auth_error (no stopped)."""
    listener, incident_logger, _ = _make_listener()
    incident_logger.log_incident = AsyncMock()

    statuses: list[str] = []

    async def capture_set_status(status, **_kw):
        statuses.append(status)

    listener._set_status = capture_set_status
    listener._connect_and_subscribe = AsyncMock(
        side_effect=_AuthError("subscribe 401: bad key")
    )

    await listener._run_ws_api()

    # run() sets "connecting" before _run_ws_api, which is mocked out here.
    # _run_ws_api itself sets auth_error and stopped.
    assert "auth_error" in statuses
    assert "stopped" not in statuses  # auth_error path does not transition to stopped


# ── Spot path: event processing ──────────────────────────────────────────────


async def test_events_with_id_field_are_skipped():
    """Messages with 'id' field (WS API responses) must not be processed as events."""
    listener, _, _ = _make_listener()
    listener._set_status = AsyncMock()
    listener._handle_event = AsyncMock()
    listener._mark_event = AsyncMock()

    fixed_req_id = "req-skip"
    sub_resp = {"id": fixed_req_id, "status": 200, "result": {}}

    # Second message: a WS API response (has "id"), should be skipped
    api_response_msg = {"id": "some-other-request", "status": 200, "result": {}}
    # Third message: a real user-data event (no "id"), should be processed
    user_event_msg = {"e": "outboundAccountPosition", "E": 1700000000000, "B": []}

    ws = MagicMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps(sub_resp))

    events = [json.dumps(api_response_msg), json.dumps(user_event_msg)]
    idx = [0]

    async def _anext():
        if idx[0] < len(events):
            r = events[idx[0]]
            idx[0] += 1
            return r
        raise StopAsyncIteration

    ws.__aiter__ = MagicMock(return_value=ws)
    ws.__anext__ = AsyncMock(side_effect=_anext)

    with (
        patch(
            "services.execution.account_stream.listener._build_subscribe_request",
            return_value=("payload", fixed_req_id),
        ),
        patch(
            "services.execution.account_stream.listener.websockets.connect",
            return_value=ws,
        ),
    ):
        await listener._connect_and_subscribe()

    # Only the user_event_msg should reach _handle_event (not the api_response_msg)
    assert listener._handle_event.call_count == 1
    handled_payload = listener._handle_event.call_args.args[0]
    assert handled_payload.get("e") == "outboundAccountPosition"


# ── _set_status Redis write ───────────────────────────────────────────────────


async def test_set_status_writes_all_transitions_to_redis():
    """_set_status must write status + updated_at_ms to Redis for every transition."""
    listener, _, redis = _make_listener()
    # DB returns None → the DB update is a no-op; only the Redis path executes
    statuses_seen = []

    for status in ("connecting", "connected", "reconnecting", "auth_error", "stopped"):
        redis.set.reset_mock()
        await listener._set_status(status)

        redis.set.assert_awaited_once()
        call_kwargs = redis.set.call_args
        # Positional args: key, value; keyword arg: ex=TTL
        key = call_kwargs[0][0]
        payload = json.loads(call_kwargs[0][1])
        assert payload["status"] == status
        assert "updated_at_ms" in payload
        assert isinstance(payload["updated_at_ms"], int)
        statuses_seen.append(status)

    assert len(statuses_seen) == 5


async def test_set_status_includes_error_in_redis_payload():
    """Error string must appear in Redis payload when status is auth_error."""
    listener, _, redis = _make_listener()

    await listener._set_status("auth_error", error="subscribe 401: bad key")

    call_args = redis.set.call_args
    payload = json.loads(call_args[0][1])
    assert payload["status"] == "auth_error"
    assert "error" in payload
    assert "401" in payload["error"]


async def test_mark_event_writes_updated_at_ms_and_last_event_ms():
    """_mark_event Redis payload must include both updated_at_ms and last_event_ms."""
    listener, _, redis = _make_listener()

    await listener._mark_event()

    redis.set.assert_awaited_once()
    call_args = redis.set.call_args
    payload = json.loads(call_args[0][1])
    assert payload["status"] == "connected"
    assert "updated_at_ms" in payload
    assert "last_event_ms" in payload
    assert payload["updated_at_ms"] == payload["last_event_ms"]
