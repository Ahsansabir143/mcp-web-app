"""AccountStreamListener — Binance user-data WebSocket for one exchange account.

Lifecycle:
  1. Get listen key via REST POST
  2. Connect to wss://{ws_base}/ws/{listen_key}
  3. Handle incoming events → AccountStateWriter → DB + Redis
  4. Keepalive listen key every listen_key_refresh_interval_s
  5. On disconnect: exponential backoff + reconnect
  6. On auth failure: mark account stream_status=auth_error, stop, log incident

Supported event types:
  Spot:    outboundAccountPosition, balanceUpdate, executionReport
  Futures: ACCOUNT_UPDATE, ORDER_TRADE_UPDATE
"""
from __future__ import annotations

import asyncio
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

# Binance listen-key paths
_SPOT_LK_PATH = "/api/v3/userDataStream"
_FUTURES_LK_PATH = "/fapi/v1/listenKey"

# HTTP status that typically indicates auth failure on listen key creation
_AUTH_HTTP_CODES = {401, 403}


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
    ) -> None:
        self._account_id = account_id
        self._account_id_str = str(account_id)
        self._api_key = api_key
        self._market_type = market_type
        self._ws_base = ws_base
        self._rest_base = rest_base
        self._session_factory = session_factory
        self._redis = redis
        self._incident_logger = incident_logger
        self._refresh_interval = listen_key_refresh_interval_s
        self._writer = AccountStateWriter(session_factory, redis, account_id)
        self._stop = asyncio.Event()
        self._listen_key: str | None = None

    def stop(self) -> None:
        self._stop.set()

    @property
    def _lk_path(self) -> str:
        return _FUTURES_LK_PATH if self._market_type == "futures" else _SPOT_LK_PATH

    async def run(self) -> None:
        await self._set_status("connecting")
        try:
            listen_key = await self._create_listen_key()
        except Exception as exc:
            log.error("listen key creation failed — stream disabled",
                      account_id=self._account_id_str, error=str(exc))
            await self._set_status("auth_error", error=str(exc))
            await self._log_incident("stream_auth_error", str(exc), "error")
            return

        self._listen_key = listen_key
        refresh_task = asyncio.create_task(self._keepalive_loop(), name=f"lk-refresh-{self._account_id_str}")
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
                        log.info("user-data stream connected",
                                 account_id=self._account_id_str, market_type=self._market_type)

                        async for raw_msg in ws:
                            if self._stop.is_set():
                                break
                            try:
                                payload = json.loads(raw_msg)
                                await self._handle_event(payload)
                                await self._mark_event()
                            except Exception as e:
                                log.warning("event handling error",
                                            account_id=self._account_id_str, error=str(e))

                except ConnectionClosed as exc:
                    log.warning("user-data stream closed",
                                account_id=self._account_id_str, error=str(exc))
                    await self._set_status("reconnecting", error=str(exc))
                except Exception as exc:
                    log.error("user-data stream error",
                              account_id=self._account_id_str, error=str(exc))
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

    async def _handle_event(self, payload: dict) -> None:
        event_type = payload.get("e", "")
        ts_ms = int(payload.get("E", time.time() * 1000))

        if event_type == "outboundAccountPosition":
            balances = payload.get("B", [])
            await self._writer.upsert_balances(balances, ts_ms)

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
            order_data = payload.get("o", {})
            await self._writer.upsert_order_from_futures_report(order_data)

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
            "last_event_ms": now_ms,
        }), ex=120)

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

    async def _create_listen_key(self) -> str:
        async with aiohttp.ClientSession(base_url=self._rest_base) as http:
            async with http.post(
                self._lk_path, headers={"X-MBX-APIKEY": self._api_key}
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
                    self._lk_path,
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
                        self._lk_path,
                        params={"listenKey": self._listen_key},
                        headers={"X-MBX-APIKEY": self._api_key},
                    ) as resp:
                        resp.raise_for_status()
                log.info("listen key refreshed", account_id=self._account_id_str)
            except Exception as exc:
                log.error("listen key keepalive failed",
                          account_id=self._account_id_str, exc_info=exc)
                await self._log_incident("stream_keepalive_failed", str(exc), "warning")

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
