from __future__ import annotations

from decimal import Decimal

from shared.redis.client import RedisClient
from shared.risk.engine import RiskEngineBase
from shared.risk.limits import RiskLimits
from shared.schemas.execution import ExecutionRequest, RiskDecision
from services.execution.controls.cooldown import CooldownControl
from services.execution.controls.kill_switch import KillSwitchControl
from services.execution.risk.checks import (
    check_max_concurrent_exposure_placeholder,
    check_max_daily_loss_placeholder,
    check_max_position_size,
    check_missing_account_context,
    check_symbol_policy,
    check_trading_mode,
    check_unsupported_order_type,
)


class ExecutionRiskEngine(RiskEngineBase):
    """Concrete risk engine for the execution service.

    Redis-backed controls (kill switch, pause, cooldown) are checked first for
    fast exit.  Synchronous structural checks (size, order type, symbol policy)
    follow.  Placeholder checks (daily loss, concurrent exposure) always pass
    when no live tracking data is available.

    The engine is stateless per-call: callers pass in runtime policy values
    (paper_only, has_credentials, daily_loss_usd, open_positions) rather than
    coupling the engine to a DB session.
    """

    def __init__(
        self,
        redis: RedisClient,
        limits: RiskLimits | None = None,
        paper_only: bool = True,
        has_credentials: bool = False,
        allowed_symbols: list[str] | None = None,
        denied_symbols: list[str] | None = None,
    ) -> None:
        self._redis = redis
        self._limits = limits or RiskLimits()
        self._paper_only = paper_only
        self._has_credentials = has_credentials
        self._allowed_symbols = allowed_symbols
        self._denied_symbols = denied_symbols or []
        self._kill_switch = KillSwitchControl(redis)
        self._cooldown = CooldownControl(redis)

    # ── RiskEngineBase ────────────────────────────────────────────────────────

    async def evaluate(
        self,
        request: ExecutionRequest,
        daily_loss_usd: Decimal | None = None,
        open_positions: int | None = None,
    ) -> RiskDecision:
        checks: dict[str, bool] = {}
        failures: list[str] = []
        metadata: dict = {}

        # 1. Kill switch — hard stop, short-circuits all other checks
        if await self.is_kill_switch_active(request.account_id):
            return RiskDecision(
                passed=False,
                checks={"kill_switch": False},
                failures=["kill_switch_active"],
                metadata={"account_id": request.account_id},
            )
        checks["kill_switch"] = True

        # 2. User pause
        if await self.is_user_paused(request.account_id):
            return RiskDecision(
                passed=False,
                checks={**checks, "user_paused": False},
                failures=["user_paused"],
            )
        checks["user_paused"] = True

        # 3. Symbol pause
        if await self.is_symbol_paused(request.account_id, request.trade_intent.symbol):
            return RiskDecision(
                passed=False,
                checks={**checks, "symbol_paused": False},
                failures=["symbol_paused"],
            )
        checks["symbol_paused"] = True

        # 4. Circuit breaker
        if await self._kill_switch.is_circuit_breaker_active(request.account_id):
            return RiskDecision(
                passed=False,
                checks={**checks, "circuit_breaker": False},
                failures=["circuit_breaker_active"],
            )
        checks["circuit_breaker"] = True

        # 5. Symbol cooldown
        if await self._cooldown.is_on_cooldown(request.account_id, request.trade_intent.symbol):
            ttl = await self._cooldown.remaining_ttl(request.account_id, request.trade_intent.symbol)
            return RiskDecision(
                passed=False,
                checks={**checks, "symbol_cooldown": False},
                failures=["symbol_on_cooldown"],
                metadata={"cooldown_remaining_s": ttl},
            )
        checks["symbol_cooldown"] = True

        # 6. Synchronous structural checks
        sync_results = [
            check_symbol_policy(request, self._allowed_symbols, self._denied_symbols),
            check_trading_mode(request, self._paper_only),
            check_missing_account_context(request, self._has_credentials),
            check_max_position_size(request, self._limits.max_position_size_usd),
            check_unsupported_order_type(request),
            check_max_daily_loss_placeholder(daily_loss_usd, self._limits.max_daily_loss_usd),
            check_max_concurrent_exposure_placeholder(
                open_positions, self._limits.max_concurrent_positions
            ),
        ]

        for r in sync_results:
            checks[r.name] = r.passed
            if not r.passed:
                failures.append(r.reason or r.name)
            if r.metadata:
                metadata[r.name] = r.metadata

        return RiskDecision(
            passed=len(failures) == 0,
            checks=checks,
            failures=failures,
            warnings=[],
            metadata=metadata,
        )

    async def is_kill_switch_active(self, account_id: str) -> bool:
        return await self._kill_switch.is_kill_switch_active(account_id)

    async def is_user_paused(self, account_id: str) -> bool:
        return await self._kill_switch.is_user_paused(account_id)

    async def is_symbol_paused(self, account_id: str, symbol: str) -> bool:
        return await self._kill_switch.is_symbol_paused(account_id, symbol)
