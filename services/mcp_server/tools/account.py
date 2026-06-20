"""Account observability MCP tools — read-only live account state."""
from __future__ import annotations

from services.execution.config import settings as exec_settings
from services.mcp_server.facades import account as account_facade


async def get_account_connection_status(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    account_id = args.get("account_id") or exec_settings.default_account_id or None
    return await account_facade.get_account_connection_status(session_factory, redis, account_id)


async def get_account_balances(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    account_id = args.get("account_id") or exec_settings.default_account_id
    if not account_id:
        return {"error": "missing_argument", "message": "'account_id' is required"}
    min_total = float(args.get("min_total", 0.0))
    return await account_facade.get_account_balances(session_factory, redis, account_id, min_total)


async def get_account_positions(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    account_id = args.get("account_id") or exec_settings.default_account_id
    if not account_id:
        return {"error": "missing_argument", "message": "'account_id' is required"}
    return await account_facade.get_account_positions(session_factory, redis, account_id)


async def get_open_orders(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    account_id = args.get("account_id") or exec_settings.default_account_id
    if not account_id:
        return {"error": "missing_argument", "message": "'account_id' is required"}
    symbol = args.get("symbol")
    limit = int(args.get("limit", 50))
    return await account_facade.get_open_orders(session_factory, account_id, symbol, limit)


async def get_recent_fills(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    account_id = args.get("account_id") or exec_settings.default_account_id
    if not account_id:
        return {"error": "missing_argument", "message": "'account_id' is required"}
    symbol = args.get("symbol")
    limit = int(args.get("limit", 20))
    return await account_facade.get_recent_fills(session_factory, account_id, symbol, limit)


async def check_live_trade_policy(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    """Dry-run live trade policy evaluation — never submits an order."""
    from decimal import Decimal
    from services.execution.adapter.live_policy import LiveTradingPolicy

    account_id = args.get("account_id") or exec_settings.default_account_id or ""
    symbol = args.get("symbol", "")
    notional_usd = float(args.get("notional_usd", 0)) or None

    if not symbol:
        return {"error": "missing_argument", "message": "'symbol' is required"}

    policy = LiveTradingPolicy.from_settings(exec_settings)
    result = policy.evaluate(
        account_id=account_id,
        symbol=symbol,
        notional_usd=Decimal(str(notional_usd)) if notional_usd else None,
        dry_run=True,
    )

    return {
        "policy_would_allow": not result.blocked_reasons,
        "live_trading_enabled": exec_settings.live_trading_enabled,
        "account_id": account_id,
        "symbol": symbol,
        "notional_usd": notional_usd,
        "blocked_reasons": result.blocked_reasons,
        "note": (
            "This is a policy check only — no order was submitted. "
            "All four gates must pass before any live order can be placed."
        ),
    }
