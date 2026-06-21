"""AccountStreamListener — Binance user-data WebSocket for one exchange account.

Spot path   : Binance WebSocket API  userDataStream.subscribe.signature
              wss://ws-api.binance.com:443/ws-api/v3  (no REST listenKey)
Futures path: REST listenKey + /ws/{listenKey} stream  (legacy, unchanged)

Lifecycle (spot):
  1. Connect to WS API endpoint
  2. Send signed userDataStream.subscribe.signature request
  3. Verify ACK (status 200); record subscriptionId
  4. Stream incoming user-data events → AccountStateWriter → DB + Redis
  5. On disconnect: exponential backoff + reconnect + resubscribe
  6. On auth failure (401/403): mark stream_status=auth_error, log incident, stop

Lifecycle (futures, unchanged):
  1. GET listen key via REST POST /fapi/v1/listenKey
  2. Connect to wss://fstream.binance.com/ws/{listenKey}
  3. Keepalive listen key every listen_key_refresh_interval_s
  4. On disconnect: reconnect to same URL; on auth error: stop
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from shared.db.models.account import ExchangeAccount
from shared.redis.keys import RedisKeys
from shared.utils.logging import get_logger
from services.execution.account_stream.state import AccountStateWriter

log = get_logger("execution.account_stream.listener")

_PING_INTERVAL = 180
_PING_TIMEOUT = 10
_CLOSE_TIMEOUT = 5
_RECONNECT_BASE_S = 2.0
_RECONNECT_MAX_S = 60.0
_RECONNECT_FACTOR = 2.0
_SUBSCRIBE_TIMEOUT_S = 15.0

# ── Spot WS API ───────────────────────────────────────────────────────────────
_WS_SUBSCRIBE_METHOD = "userDataStream.subscribe.signature"
_AUTH_WS_STATUSES = {401, 403}

# ── Futures REST listenKey (unchanged) ────────────────────────────────────────
_FUTURES_LK_PATH = "/fapi/v1/listenKey"
_AUTH_HTTP_CODES = {401, 403}


class _AuthError(Exception):
    """Non-retryable authentication or permission failure."""


# ── Module-level pure functions (testable without instantiation) ──────────────

def _build_subscribe_request(
    api_key: str,
    api_secret: str,
    *,
    ts: int | None = None,
    req_id: str | None = None,
) -> tuple[str, str]:
    """Build a signed WS API userDataStream.subscribe.signature request.

    Returns ``(json_string, request_id)``.  ``ts`` and ``req_id`` can be
    injected by tests for deterministic output; in production both are
    generated automatically.
    """
    if ts is None:
        ts = int(time.time() * 1000)
    if req_id is None:
        req_id = str(uuid.uuid4())

    qs = f"apiKey={api_key}&timestamp={ts}"
    sig = hmac.new(
        api_secret.encode("utf-8"),
        qs.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    payload = {
        "id": req_id,
        "method": _WS_SUBSCRIBE_METHOD,
        "params": {
            "apiKey": api_key,
            "timestamp": ts,
            "signature": sig,
        },
    }
    return json.dumps(payload), req_id


def _check_subscribe_response(resp: dict, expected_id: str) -> None:
    """Validate a WS API subscribe ACK.

    Raises _AuthError for 401/403 (non-retryable).
    Raises RuntimeError for other non-200 responses.
    Returns silently for status 200, or if resp.id doesn't match.
    """
    if resp.get("id") != expected_id:
        return
    status = resp.get("status", 0)
    if status == 200:
        return
    err = resp.get("error") or {}
    code = err.get("code", "?") if isinstance(err, dict) else "?"
    msg = err.get("msg", str(resp)) if isinstance(err, dict) else str(resp)
    err_text = f"subscribe {status}: {code} {msg}"
    if status in _AUTH_WS_STATUSES:
        raise _AuthError(err_text)
    raise RuntimeError(err_text)


# ── Listener ──────────────────────────────────────────────────────────────────

class AccountStreamListener:
    """Manages the full user-data stream lifecycle for one exchange account."""

    def __init__(
        self,
        account_id: uuid.UUID,
        api_key: str,
        api_secret: str,
        market_type: str,
        ws_base: str,
        rest_base: str,
        session_factory,
        redis,
        incident_logger=None,
        listen_key_refresh_interval_s: float = 1800.0,
        ws_api_base: str = "wss://ws-api.binance.com:443/ws-api/v3",
    ) -> None:
        self._account_id = account_id
        self._account_id_str = str(account_id)
        self._api_key = api_key
        self._api_secret = api_secret
        self._market_type = market_type
        self._ws_base = ws_base          # futures stream: {ws_base}/ws/{listen_key}
        self._rest_base = rest_base      # futures REST listenKey endpoint
        self._ws_api_base = ws_api_base  # spot WS API endpoint
        self._session_factory = session_factory
        self._redis = redis
        self._incident_logger = incident_logger
        self._refresh_interval = listen_key_refresh_interval_s
        self._writer = AccountStateWriter(session_factory, redis, account_id)
        self._stop = asyncio.Event()
        self._listen_key: str | None = None        # futures only
        self._subscription_id: str | None = None   # spot WS API only

    def stop(self) -> None:
        self._stop.set()

    # ── Entry point ───────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self._set_status("connecting")
        if self._market_type == "spot":
            await self._run_ws_api()
        else:
            await self._run_legacy_stream()

    # ── Spot path: Binance WebSocket API ─────────────────────────────────────

    async def _run_ws_api(self) -> None:
        """Spot user-data stream via Binance WS API (no REST listenKey)."""
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_and_subscribe()
                attempt = 0
            except _AuthError as exc:
                log.error(
                    "stream auth failed — stream disabled",
                    account_id=self._account_id_str,
                    error=str(exc),
                )
                await self._set_status("auth_error", error=str(exc))
                await self._log_incident("stream_auth_error", str(exc), "error")
                return
            except ConnectionClosed as exc:
                log.warning(
                    "user-data stream closed",
                    account_id=self._account_id_str,
                    error=str(exc),
                )
                await self._set_status("reconnecting", error=str(exc))
            except Exception as exc:
                log.error(
                    "user-data stream error",
                    account_id=self._account_id_str,
                    error=str(exc),
                )
                await self._set_status("reconnecting", error=str(exc))

            if self._stop.is_set():
                break

            delay = min(_RECONNECT_MAX_S, _RECONNECT_BASE_S * (_RECONNECT_FACTOR ** attempt))
            attempt += 1
            log.info(f"reconnecting in {delay:.1f}s", account_id=self._account_id_str)
            await asyncio.sleep(delay)

        await self._set_status("stopped")
        log.info("user-data stream stopped", account_id=self._account_id_str)

    async def _connect_and_subscribe(self) -> None:
        """Open one WS API connection, subscribe, and stream events until disconnect."""
        async with websockets.connect(
            self._ws_api_base,
            ping_interval=_PING_INTERVAL,
            ping_timeout=_PING_TIMEOUT,
            close_timeout=_CLOSE_TIMEOUT,
        ) as ws:
            req_json, req_id = _build_subscribe_request(self._api_key, self._api_secret)
            await ws.send(req_json)

            raw_ack = await asyncio.wait_for(ws.recv(), timeout=_SUBSCRIBE_TIMEOUT_S)
            ack = json.loads(raw_ack)
            _check_subscribe_response(ack, req_id)

            self._subscription_id = (
                ack.get("result", {}).get("subscriptionId")
                if isinstance(ack.get("result"), dict)
                else None
            )

            await self._set_status("connected")
            log.info(
                "user-data stream connected (WS API)",
                account_id=self._account_id_str,
                market_type=self._market_type,
                subscription_id=self._subscription_id,
            )

            async for raw_msg in ws:
                if self._stop.is_set():
                    break
                payload = json.loads(raw_msg)
                if "id" in payload:
                    # Response to a request we sent (not a user-data event)
                    continue
                try:
                    await self._handle_event(payload)
                    await self._mark_event()
                except Exception as exc:
                    log.warning(
                        "event handling error",
                        account_id=self._account_id_str,
                        error=str(exc),
                    )

    # ── Futures path: REST listenKey + stream WS (original) ──────────────────

    async def _run_legacy_stream(self) -> None:
        """Futures user-data stream via REST listenKey (original implementation)."""
        try:
            listen_key = await self._create_listen_key()
        except Exception as exc:
            log.error(
                "listen key creation failed — stream disabled",
                account_id=self._account_id_str,
                error=str(exc),
            )
            await self._set_status("auth_error", error=str(exc))
            await self._log_incident("stream_auth_error", str(exc), "error")
            return

        self._listen_key = listen_key
        refresh_task = asyncio.create_task(
            self._keepalive_loop(), name=f"lk-refresh-{self._account_id_str}"
        )
        attempt = 0

        try:
            while not self._stop.is_set():
                url = f"{self._ws_base}/ws/{listen_key}"
                await self._set_status("connecting")
                try:
                    async with websockets.connect(
                        url,
                        ping_interval=_PING_INTERVAL,
                        ping_timeout=_PING_TIMEOUT,
                        close_timeout=_CLOSE_TIMEOUT,
                    ) as ws:
                        await self._set_status("connected")
                        attempt = 0
                        log.info(
                            "user-data stream connected",
                            account_id=self._account_id_str,
                            market_type=self._market_type,
                        )
                        async for raw_msg in ws:
                            if self._stop.is_set():
                                break
                            try:
                                payload = json.loads(raw_msg)
                                await self._handle_event(payload)
                                await self._mark_event()
                            except Exception as exc:
                                log.warning(
                                    "event handling error",
                                    account_id=self._account_id_str,
                                    error=str(exc),
                                )
                except ConnectionClosed as exc:
                    log.warning(
                        "user-data stream closed",
                        account_id=self._account_id_str,
                        error=str(exc),
                    )
                    await self._set_status("reconnecting", error=str(exc))
                except Exception as exc:
                    log.error(
                        "user-data stream error",
                        account_id=self._account_id_str,
                        error=str(exc),
                    )
                    await self._set_status("reconnecting", error=str(exc))

                if self._stop.is_set():
                    break

                delay = min(_RECONNECT_MAX_S, _RECONNECT_BASE_S * (_RECONNECT_FACTOR ** attempt))
                attempt += 1
                log.info(f"reconnecting in {delay:.1f}s", account_id=self._account_id_str)
                await asyncio.sleep(delay)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
            await self._delete_listen_key()
            await self._set_status("stopped")
            log.info("user-data stream stopped", account_id=self._account_id_str)

    # ── Event handling (shared by both paths) ─────────────────────────────────

    async def _handle_event(self, payload: dict) -> None:
        event_type = payload.get("e", "")
        ts_ms = int(payload.get("E", time.time() * 1000))

        if event_type == "outboundAccountPosition":
            await self._writer.upsert_balances(payload.get("B", []), ts_ms)

        elif event_type == "balanceUpdate":
            asset = payload.get("a", "")
            delta = str(payload.get("d", "0"))
            if asset:
                await self._writer.upsert_balances(
                    [{"a": asset, "f": delta, "l": "0"}], ts_ms
                )

        elif event_type == "executionReport":
            await self._writer.upsert_order_from_execution_report(payload)

        elif event_type == "ACCOUNT_UPDATE":
            data = payload.get("a", {})
            balances = data.get("B", [])
            positions = data.get("P", [])
            if balances:
                await self._writer.upsert_balances(balances, ts_ms)
            if positions:
                await self._writer.upsert_positions(positions, ts_ms)

        elif event_type == "ORDER_TRADE_UPDATE":
            await self._writer.upsert_order_from_futures_report(payload.get("o", {}))

    async def _mark_event(self) -> None:
        now_ms = int(time.time() * 1000)
        try:
            async with self._session_factory() as session:
                acct = await session.get(ExchangeAccount, self._account_id)
                if acct:
                    acct.stream_last_event_ms = now_ms
                    await session.commit()
        except Exception as exc:
            log.warning("stream_last_event_ms update failed", exc_info=exc)
        cache_key = RedisKeys.account_stream_status(self._account_id_str)
        await self._redis.set(cache_key, json.dumps({
            "status": "connected",
            "updated_at_ms": now_ms,
            "last_event_ms": now_ms,
        }), ex=120)

    # TTL per status — non-event statuses use longer TTLs since they aren't refreshed by events
    _STATUS_REDIS_TTL: dict[str, int] = {
        "connecting": 300,
        "connected": 120,
        "reconnecting": 300,
        "auth_error": 3600,
        "stopped": 3600,
    }

    async def _set_status(self, status: str, error: str | None = None) -> None:
        try:
            async with self._session_factory() as session:
                acct = await session.get(ExchangeAccount, self._account_id)
                if acct:
                    acct.stream_status = status
                    if error is not None:
                        acct.stream_error = error[:512]
                    elif status == "connected":
                        acct.stream_error = None
                    await session.commit()
        except Exception as exc:
            log.warning("stream status update failed", exc_info=exc)
        # Write all status transitions to Redis so MCP can observe non-event states
        try:
            now_ms = int(time.time() * 1000)
            cache_key = RedisKeys.account_stream_status(self._account_id_str)
            payload: dict = {"status": status, "updated_at_ms": now_ms}
            if error:
                payload["error"] = error[:512]
            ttl = self._STATUS_REDIS_TTL.get(status, 300)
            await self._redis.set(cache_key, json.dumps(payload), ex=ttl)
        except Exception as exc:
            log.warning("stream status Redis write failed", exc_info=exc)

    # ── Futures-only: REST listenKey lifecycle ────────────────────────────────

    async def _create_listen_key(self) -> str:
        async with aiohttp.ClientSession(base_url=self._rest_base) as http:
            async with http.post(
                _FUTURES_LK_PATH, headers={"X-MBX-APIKEY": self._api_key}
            ) as resp:
                if resp.status in _AUTH_HTTP_CODES:
                    raise PermissionError(f"listen key HTTP {resp.status}")
                resp.raise_for_status()
                data = await resp.json()
                return data["listenKey"]

    async def _delete_listen_key(self) -> None:
        if not self._listen_key:
            return
        try:
            async with aiohttp.ClientSession(base_url=self._rest_base) as http:
                async with http.delete(
                    _FUTURES_LK_PATH,
                    params={"listenKey": self._listen_key},
                    headers={"X-MBX-APIKEY": self._api_key},
                ) as resp:
                    await resp.read()
        except Exception as exc:
            log.warning("listen key delete failed", exc_info=exc)

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            if not self._listen_key:
                continue
            try:
                async with aiohttp.ClientSession(base_url=self._rest_base) as http:
                    async with http.put(
                        _FUTURES_LK_PATH,
                        params={"listenKey": self._listen_key},
                        headers={"X-MBX-APIKEY": self._api_key},
                    ) as resp:
                        resp.raise_for_status()
                log.info("listen key refreshed", account_id=self._account_id_str)
            except Exception as exc:
                log.error(
                    "listen key keepalive failed",
                    account_id=self._account_id_str,
                    exc_info=exc,
                )
                await self._log_incident("stream_keepalive_failed", str(exc), "warning")

    # ── Incident logging (shared) ─────────────────────────────────────────────

    async def _log_incident(self, incident_type: str, message: str, severity: str) -> None:
        if self._incident_logger is None:
            return
        try:
            await self._incident_logger.log_incident(
                incident_type=incident_type,
                description=message,
                severity=severity,
                context={
                    "account_id": self._account_id_str,
                    "market_type": self._market_type,
                },
            )
        except Exception as exc:
            log.error("incident log failed", exc_info=exc)
