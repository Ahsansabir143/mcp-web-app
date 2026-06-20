"""LiveTradingPolicy — evaluates all gates before any real order submission.

All four gates MUST pass. If any fails, the order is blocked.

Gates (evaluated in order):
  1. LIVE_TRADING_ENABLED=true           master switch
  2. account_id in account allowlist     per-account allowlist
  3. symbol in symbol allowlist          per-symbol allowlist
  4. notional <= LIVE_MAX_NOTIONAL_USD   per-order size cap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class PolicyResult:
    allowed: bool
    blocked_reasons: list[str] = field(default_factory=list)
    is_dry_run: bool = False


class LiveTradingPolicy:
    """Evaluates live trading gates against a parsed configuration."""

    def __init__(
        self,
        live_trading_enabled: bool = False,
        account_allowlist: list[str] | None = None,
        symbol_allowlist: list[str] | None = None,
        max_notional_usd: float = 100.0,
    ) -> None:
        self._enabled = live_trading_enabled
        self._account_allowlist: frozenset[str] = (
            frozenset(a.strip() for a in account_allowlist if a.strip())
            if account_allowlist
            else frozenset()
        )
        self._symbol_allowlist: frozenset[str] = (
            frozenset(s.strip().upper() for s in symbol_allowlist if s.strip())
            if symbol_allowlist
            else frozenset()
        )
        self._max_notional = Decimal(str(max_notional_usd))

    @classmethod
    def from_settings(cls, settings) -> "LiveTradingPolicy":
        account_list = [
            a for a in settings.live_trading_account_allowlist.split(",")
            if a.strip()
        ] if settings.live_trading_account_allowlist else []
        symbol_list = [
            s for s in settings.live_trading_symbol_allowlist.split(",")
            if s.strip()
        ] if settings.live_trading_symbol_allowlist else []
        return cls(
            live_trading_enabled=settings.live_trading_enabled,
            account_allowlist=account_list,
            symbol_allowlist=symbol_list,
            max_notional_usd=settings.live_max_notional_usd,
        )

    def evaluate(
        self,
        account_id: str,
        symbol: str,
        notional_usd: Decimal | None = None,
        dry_run: bool = False,
    ) -> PolicyResult:
        reasons: list[str] = []

        if not self._enabled:
            reasons.append("live_trading_disabled: LIVE_TRADING_ENABLED is false")

        if not self._account_allowlist:
            reasons.append(
                f"account_not_allowed: no accounts in allowlist (LIVE_TRADING_ACCOUNT_ALLOWLIST is empty)"
            )
        elif account_id not in self._account_allowlist:
            reasons.append(
                f"account_not_allowed: account_id '{account_id}' not in allowlist"
            )

        if not self._symbol_allowlist:
            reasons.append("symbol_not_allowed: symbol allowlist is empty")
        elif symbol.upper() not in self._symbol_allowlist:
            reasons.append(
                f"symbol_not_allowed: '{symbol}' not in allowlist {sorted(self._symbol_allowlist)}"
            )

        if notional_usd is not None and notional_usd > self._max_notional:
            reasons.append(
                f"notional_exceeds_cap: {notional_usd} USD > cap {self._max_notional} USD"
            )

        return PolicyResult(
            allowed=len(reasons) == 0 and not dry_run,
            blocked_reasons=reasons,
            is_dry_run=dry_run,
        )
