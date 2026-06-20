"""Binance REST connectivity checker — validates credentials with a read-only endpoint.

The check uses GET /api/v3/account (spot) or GET /fapi/v2/account (futures).
These endpoints require a valid API key with at minimum "Enable Reading" permission
and produce no side effects.

Incident types produced:
  auth_key_invalid     — -2014/-1022: key format invalid or signature mismatch
  auth_timestamp_drift — -1021: local clock differs from Binance clock by > recvWindow
  auth_ip_restricted   — -2015: caller IP not on the key's allowed-IP list
  auth_perm_error      — -2012: key lacks required read permission
  connectivity_network_error — timeout or connection refused
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import httpx

from shared.utils.logging import get_logger

log = get_logger("execution.account.connectivity")

_SPOT_PATH = "/api/v3/account"
_FUTURES_PATH = "/fapi/v2/account"

# Binance-documented API error codes
_CODE_INVALID_KEY = -2014
_CODE_INVALID_SIG = -1022
_CODE_TIMESTAMP = -1021
_CODE_IP_BANNED = -2015
_CODE_NO_PERM = -2012


def _sign(params: dict, secret: str) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


async def check_account_connectivity(
    api_key: str,
    api_secret: str,
    market_type: str = "spot",
    rest_base: str = "https://api.binance.com",
) -> dict:
    """Check exchange connectivity with a harmless authenticated REST call.

    Returns:
    {
        "status": "connected"|"auth_error"|"ip_restricted"|"perm_error"|
                  "timestamp_error"|"network_error",
        "code": int | None,
        "message": str,
        "latency_ms": int,
    }
    """
    path = _FUTURES_PATH if market_type == "futures" else _SPOT_PATH
    params: dict = {
        "timestamp": int(time.time() * 1000),
        "recvWindow": 5000,
    }
    params["signature"] = _sign(params, api_secret)

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(base_url=rest_base, timeout=10.0) as client:
            resp = await client.get(path, params=params, headers={"X-MBX-APIKEY": api_key})
        latency_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code == 200:
            return {"status": "connected", "code": 200, "message": "OK", "latency_ms": latency_ms}

        try:
            body = resp.json()
            code = int(body.get("code", resp.status_code))
            msg = body.get("msg", "")
        except Exception:
            code = resp.status_code
            msg = resp.text or "unknown error"

        if code in (_CODE_INVALID_KEY, _CODE_INVALID_SIG):
            return {"status": "auth_error", "code": code, "message": msg, "latency_ms": latency_ms}
        if code == _CODE_TIMESTAMP:
            return {"status": "timestamp_error", "code": code, "message": msg, "latency_ms": latency_ms}
        if code == _CODE_IP_BANNED:
            return {"status": "ip_restricted", "code": code, "message": msg, "latency_ms": latency_ms}
        if code == _CODE_NO_PERM:
            return {"status": "perm_error", "code": code, "message": msg, "latency_ms": latency_ms}
        return {"status": "auth_error", "code": code, "message": msg, "latency_ms": latency_ms}

    except httpx.TimeoutException:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"status": "network_error", "code": None, "message": "timeout", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"status": "network_error", "code": None, "message": str(exc), "latency_ms": latency_ms}


_INCIDENT_TYPE_MAP = {
    "auth_error": "auth_key_invalid",
    "ip_restricted": "auth_ip_restricted",
    "perm_error": "auth_perm_error",
    "timestamp_error": "auth_timestamp_drift",
    "network_error": "connectivity_network_error",
}


async def check_and_persist(
    account_id: uuid.UUID,
    api_key: str,
    api_secret: str,
    session_factory,
    incident_logger=None,
    rest_base: str = "https://api.binance.com",
    market_type: str = "spot",
) -> dict:
    """Run connectivity check, update account record, and log incident on failure."""
    from shared.db.models.account import ExchangeAccount

    now_ms = int(time.time() * 1000)
    result = await check_account_connectivity(api_key, api_secret, market_type, rest_base)
    status = result["status"]

    try:
        async with session_factory() as session:
            acct = await session.get(ExchangeAccount, account_id)
            if acct is not None:
                acct.connection_status = status
                acct.last_connectivity_check_ms = now_ms
                await session.commit()
    except Exception as exc:
        log.error("connectivity status persist failed", exc_info=exc)

    if status != "connected" and incident_logger is not None:
        severity = "error" if status in ("auth_error", "ip_restricted", "perm_error") else "warning"
        try:
            await incident_logger.log_incident(
                incident_type=_INCIDENT_TYPE_MAP.get(status, "connectivity_unknown"),
                description=f"Exchange connectivity check failed [{status}]: {result['message']}",
                severity=severity,
                context={
                    "account_id": str(account_id),
                    "market_type": market_type,
                    "error_code": result.get("code"),
                    "error_message": result.get("message"),
                    "latency_ms": result.get("latency_ms"),
                },
            )
        except Exception as exc:
            log.error("failed to log connectivity incident", exc_info=exc)

    log.info("connectivity check", account_id=str(account_id), status=status,
             latency_ms=result.get("latency_ms"))
    return result
