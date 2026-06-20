"""Read-only MCP tools: market snapshot, strategy listing, executions, incidents."""
from __future__ import annotations

from services.mcp_server.facades import execution as exec_facade
from services.mcp_server.facades import market as market_facade
from services.mcp_server.facades import strategy as strat_facade


async def get_symbol_snapshot(args: dict, *, redis, session_factory, user_identity=None) -> dict:
    symbol = args.get("symbol", "")
    market_type = args.get("market_type", "futures")
    if not symbol:
        return {"error": "missing_argument", "message": "'symbol' is required"}
    return await market_facade.get_symbol_snapshot(redis, market_type, symbol)


async def list_strategies(args: dict, *, redis, session_factory, user_identity=None) -> dict:
    symbol_filter = args.get("symbol")
    state_filter = args.get("state")
    limit = int(args.get("limit", 50))
    rows = await strat_facade.list_strategies(
        session_factory,
        symbol_filter=symbol_filter,
        state_filter=state_filter,
        limit=limit,
    )
    return {"strategies": rows, "count": len(rows)}


async def get_strategy_details(args: dict, *, redis, session_factory, user_identity=None) -> dict:
    strategy_id = args.get("strategy_id", "")
    if not strategy_id:
        return {"error": "missing_argument", "message": "'strategy_id' is required"}
    detail = await strat_facade.get_strategy_details(session_factory, strategy_id)
    if detail is None:
        return {"error": "not_found", "message": f"Strategy '{strategy_id}' not found"}
    return detail


async def get_recent_executions(args: dict, *, redis, session_factory, user_identity=None) -> dict:
    strategy_id = args.get("strategy_id")
    symbol = args.get("symbol")
    limit = int(args.get("limit", 20))
    rows = await exec_facade.get_recent_executions(
        session_factory,
        strategy_id=strategy_id,
        symbol=symbol,
        limit=limit,
    )
    return {"executions": rows, "count": len(rows)}


async def get_incidents(args: dict, *, redis, session_factory, user_identity=None) -> dict:
    symbol = args.get("symbol")
    since_ts = args.get("since_ts")
    since_ms = int(since_ts) if since_ts else None
    limit = int(args.get("limit", 50))
    rows = await exec_facade.get_incidents(
        session_factory,
        symbol=symbol,
        since_ts=since_ms,
        limit=limit,
    )
    return {"incidents": rows, "count": len(rows)}
