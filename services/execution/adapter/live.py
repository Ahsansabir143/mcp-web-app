"""LiveExecutionAdapter — gated real-order submission adapter.

IMPORTANT: This adapter will NEVER submit a live order unless ALL four
policy gates pass (see LiveTradingPolicy). By default, live trading is
disabled (LIVE_TRADING_ENABLED=false) and both account and symbol
allowlists are empty, making live submission impossible.

Dry-run / preview mode:
  If the request metadata contains {"dry_run": true}, the adapter validates
  the policy and returns a preview dict without submitting — useful for
  confirming what would happen before enabling live trading.

When blocked:
  Returns AdapterResponse(success=False, error="blocked_by_policy: ...") and
  logs an incident via the incident_logger.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

import httpx

from shared.schemas.execution import ExecutionRequest
from shared.utils.logging import get_logger
from services.execution.adapter.base import AdapterResponse, ExecutionAdapterBase
from services.execution.adapter.live_policy import LiveTradingPolicy, PolicyResult

log = get_logger("execution.adapter.live")


class LiveExecutionAdapter(ExecutionAdapterBase):
    """
    Gated live order adapter.

    Gate order (all required):
      1. LIVE_TRADING_ENABLED=true
      2. account_id in account allowlist
      3. symbol in symbol allowlist
      4. notional <= LIVE_MAX_NOTIONAL_USD

    Dry-run mode returns a preview without submitting even when all gates pass.
    Live submission requires an api_key+api_secret obtained from the credential store.
    """

    def __init__(
        self,
        policy: LiveTradingPolicy,
        api_key: str = "",
        api_secret: str = "",
        rest_base: str = "https://api.binance.com",
        incident_logger=None,
    ) -> None:
        self._policy = policy
        self._api_key = api_key
        self._api_secret = api_secret
        self._rest_base = rest_base
        self._incident_logger = incident_logger

    def adapter_name(self) -> str:
        return "live"

    async def submit(
        self,
        request: ExecutionRequest,
        client_order_id: str,
    ) -> AdapterResponse:
        intent = request.trade_intent
        dry_run = bool((intent.metadata or {}).get("dry_run", False))

        notional = intent.size_usd
        if notional is None and intent.limit_price is not None:
            notional = intent.size * intent.limit_price

        policy_result: PolicyResult = self._policy.evaluate(
            account_id=request.account_id,
            symbol=intent.symbol,
            notional_usd=notional,
            dry_run=dry_run,
        )

        if not policy_result.allowed:
            reasons_str = "; ".join(policy_result.blocked_reasons)
            error_msg = f"blocked_by_policy: {reasons_str}"
            if policy_result.is_dry_run:
                error_msg = f"dry_run_preview: policy={'would_allow' if not policy_result.blocked_reasons else 'would_block'}; {reasons_str}"
            await self._log_policy_block(request, client_order_id, policy_result)
            return AdapterResponse(
                success=False,
                client_order_id=client_order_id,
                exchange_order_id=None,
                error=error_msg,
            )

        if dry_run:
            # Policy passed — return preview without submitting
            return AdapterResponse(
                success=False,
                client_order_id=client_order_id,
                exchange_order_id=None,
                error=(
                    f"dry_run_preview: all policy gates pass; "
                    f"live order WOULD be submitted for {intent.symbol} "
                    f"{intent.side.value} {intent.size} (notional≈{notional} USD). "
                    "Remove dry_run=true from metadata to execute."
                ),
            )

        # All gates passed and not dry-run: submit real order
        return await self._submit_to_exchange(request, client_order_id)

    async def _submit_to_exchange(
        self,
        request: ExecutionRequest,
        client_order_id: str,
    ) -> AdapterResponse:
        intent = request.trade_intent
        params: dict = {
            "symbol": intent.symbol,
            "side": intent.side.value,
            "type": intent.order_type.value,
            "quantity": str(intent.size),
            "newClientOrderId": client_order_id,
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000,
        }
        if intent.limit_price is not None:
            params["price"] = str(intent.limit_price)
            params["timeInForce"] = "GTC"

        signature = self._sign(params)
        params["signature"] = signature

        try:
            async with httpx.AsyncClient(base_url=self._rest_base, timeout=10.0) as client:
                resp = await client.post(
                    "/api/v3/order",
                    params=params,
                    headers={"X-MBX-APIKEY": self._api_key},
                )
            if resp.status_code == 200:
                body = resp.json()
                fills = body.get("fills", [])
                fill_price = None
                fill_qty = None
                commission = None
                commission_asset = "USDT"
                if fills:
                    fill_price = Decimal(str(fills[0].get("price", "0")))
                    fill_qty = Decimal(str(body.get("executedQty", "0")))
                    commission = sum(Decimal(str(f.get("commission", "0"))) for f in fills)
                    commission_asset = fills[0].get("commissionAsset", "USDT")
                elif body.get("status") == "FILLED":
                    fill_qty = Decimal(str(body.get("executedQty", "0")))
                    cummulative = body.get("cummulativeQuoteQty", "0")
                    if fill_qty and fill_qty > 0:
                        fill_price = Decimal(cummulative) / fill_qty

                return AdapterResponse(
                    success=True,
                    client_order_id=client_order_id,
                    exchange_order_id=str(body.get("orderId", "")),
                    fill_price=fill_price,
                    fill_quantity=fill_qty,
                    commission=commission,
                    commission_asset=commission_asset,
                    raw_response=body,
                )
            else:
                try:
                    err = resp.json()
                except Exception:
                    err = {"msg": resp.text}
                return AdapterResponse(
                    success=False,
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    error=f"exchange_error_{resp.status_code}: {err.get('msg', 'unknown')}",
                )
        except Exception as exc:
            return AdapterResponse(
                success=False,
                client_order_id=client_order_id,
                exchange_order_id=None,
                error=f"network_error: {exc}",
            )

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _log_policy_block(
        self,
        request: ExecutionRequest,
        client_order_id: str,
        result: PolicyResult,
    ) -> None:
        log.warning(
            "live order blocked by policy",
            account_id=request.account_id,
            symbol=request.trade_intent.symbol,
            blocked_reasons=result.blocked_reasons,
        )
        if self._incident_logger is None:
            return
        try:
            await self._incident_logger.log_incident(
                incident_type="live_trade_blocked_by_policy",
                description=f"Live order blocked: {'; '.join(result.blocked_reasons)}",
                severity="info",
                context={
                    "account_id": request.account_id,
                    "symbol": request.trade_intent.symbol,
                    "side": request.trade_intent.side.value,
                    "client_order_id": client_order_id,
                    "blocked_reasons": result.blocked_reasons,
                    "is_dry_run": result.is_dry_run,
                },
            )
        except Exception as exc:
            log.error("policy block incident log failed", exc_info=exc)
