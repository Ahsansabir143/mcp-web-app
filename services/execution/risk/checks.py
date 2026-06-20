from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from shared.schemas.enums import TradingMode
from shared.schemas.execution import ExecutionRequest


@dataclass
class CheckResult:
    name: str
    passed: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Synchronous checks (no I/O) ───────────────────────────────────────────────


def check_max_position_size(
    request: ExecutionRequest,
    max_usd: Decimal,
) -> CheckResult:
    """Reject trades whose notional exceeds the account limit."""
    size_usd = request.trade_intent.size_usd
    if size_usd is None:
        lp = request.trade_intent.limit_price
        if lp and lp > 0:
            size_usd = request.trade_intent.size * lp
        else:
            return CheckResult("max_position_size", True, "size_usd_unavailable")
    passed = size_usd <= max_usd
    return CheckResult(
        "max_position_size",
        passed,
        reason="" if passed else f"size_usd={size_usd} > max={max_usd}",
        metadata={"size_usd": str(size_usd), "max_usd": str(max_usd)},
    )


def check_unsupported_order_type(
    request: ExecutionRequest,
    allowed: set[str] | None = None,
) -> CheckResult:
    """Reject order types not supported by the current adapter."""
    supported = allowed or {"MARKET", "LIMIT"}
    ot = (
        request.trade_intent.order_type.value
        if hasattr(request.trade_intent.order_type, "value")
        else str(request.trade_intent.order_type)
    )
    passed = ot in supported
    return CheckResult(
        "unsupported_order_type",
        passed,
        reason="" if passed else f"order_type={ot!r} not in {supported}",
        metadata={"order_type": ot},
    )


def check_symbol_policy(
    request: ExecutionRequest,
    allowed_symbols: list[str] | None,
    denied_symbols: list[str],
) -> CheckResult:
    """Enforce account-level symbol allow/deny policy."""
    symbol = request.trade_intent.symbol
    if symbol in denied_symbols:
        return CheckResult("symbol_policy", False, f"symbol {symbol!r} is denied")
    if allowed_symbols is not None and symbol not in allowed_symbols:
        return CheckResult("symbol_policy", False, f"symbol {symbol!r} not in allowed list")
    return CheckResult("symbol_policy", True)


def check_trading_mode(
    request: ExecutionRequest,
    paper_only: bool,
) -> CheckResult:
    """Block live execution on accounts restricted to paper trading."""
    if paper_only and request.trading_mode == TradingMode.LIVE:
        return CheckResult(
            "trading_mode",
            False,
            "account is paper_only but intent requests live execution",
        )
    return CheckResult("trading_mode", True)


def check_missing_account_context(
    request: ExecutionRequest,
    has_credentials: bool,
) -> CheckResult:
    """Require credentials for live execution."""
    if request.trading_mode == TradingMode.LIVE and not has_credentials:
        return CheckResult(
            "missing_account_context",
            False,
            "live mode requires API credentials but none are configured",
        )
    return CheckResult("missing_account_context", True)


def check_max_daily_loss_placeholder(
    daily_loss_usd: Decimal | None,
    max_daily_loss_usd: Decimal,
) -> CheckResult:
    """Placeholder: pass when no daily-loss tracking data is available."""
    if daily_loss_usd is None:
        return CheckResult("max_daily_loss", True, "no_daily_loss_data")
    passed = daily_loss_usd < max_daily_loss_usd
    return CheckResult(
        "max_daily_loss",
        passed,
        reason="" if passed else f"daily_loss={daily_loss_usd} >= max={max_daily_loss_usd}",
        metadata={"daily_loss_usd": str(daily_loss_usd), "max_usd": str(max_daily_loss_usd)},
    )


def check_max_concurrent_exposure_placeholder(
    open_positions: int | None,
    max_concurrent: int,
) -> CheckResult:
    """Placeholder: pass when no live position count is available."""
    if open_positions is None:
        return CheckResult("max_concurrent_exposure", True, "no_position_data")
    passed = open_positions < max_concurrent
    return CheckResult(
        "max_concurrent_exposure",
        passed,
        reason="" if passed else f"open_positions={open_positions} >= max={max_concurrent}",
        metadata={"open_positions": open_positions, "max_concurrent": max_concurrent},
    )
