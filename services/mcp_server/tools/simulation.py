"""Simulation MCP tools."""
from __future__ import annotations

from services.mcp_server.facades import strategy as strat_facade


async def simulate_strategy_on_snapshot(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    strategy_id = args.get("strategy_id", "")
    symbol = args.get("symbol", "")
    market_type = args.get("market_type", "futures")

    if not strategy_id:
        return {"error": "missing_argument", "message": "'strategy_id' is required"}
    if not symbol:
        return {"error": "missing_argument", "message": "'symbol' is required"}

    return await strat_facade.simulate_strategy_on_snapshot(
        session_factory, redis, strategy_id, symbol, market_type
    )


async def simulate_strategy_on_range(
    args: dict, *, redis, session_factory, user_identity=None
) -> dict:
    """Stub: range simulation requires historical snapshot replay (deferred)."""
    return {
        "status": "not_implemented",
        "message": (
            "Full range simulation requires historical snapshot replay which is "
            "deferred to a future phase. Use 'simulate_strategy_on_snapshot' to "
            "evaluate against the latest available analytics snapshot."
        ),
        "available_tool": "simulate_strategy_on_snapshot",
        "deferred_reason": "historical_snapshot_store_not_implemented",
    }
