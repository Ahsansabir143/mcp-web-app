"""Write / control MCP tools (paper-safe)."""
from __future__ import annotations

from services.mcp_server.facades import execution as exec_facade
from services.mcp_server.facades import strategy as strat_facade


async def request_paper_trade(args: dict, *, redis, session_factory) -> dict:
    strategy_id = args.get("strategy_id", "")
    symbol = args.get("symbol", "")
    side = args.get("side", "")
    size_usd = args.get("size_usd")
    size = args.get("size")
    reason = args.get("reason", "")

    for name, val in [("strategy_id", strategy_id), ("symbol", symbol), ("side", side)]:
        if not val:
            return {"error": "missing_argument", "message": f"'{name}' is required"}

    return await exec_facade.request_paper_trade(
        session_factory=session_factory,
        redis=redis,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        size_usd=float(size_usd) if size_usd is not None else None,
        size=float(size) if size is not None else None,
        reason=reason,
    )


async def update_strategy_state(args: dict, *, redis, session_factory) -> dict:
    strategy_id = args.get("strategy_id", "")
    target_state = args.get("target_state", "")
    justification = args.get("justification", "")

    for name, val in [("strategy_id", strategy_id), ("target_state", target_state)]:
        if not val:
            return {"error": "missing_argument", "message": f"'{name}' is required"}

    if not justification:
        return {
            "error": "missing_argument",
            "message": "'justification' is required for audit logging.",
        }

    approval_level = args.get("approval_level")
    return await strat_facade.update_strategy_state(
        session_factory=session_factory,
        strategy_id=strategy_id,
        target_state=target_state,
        justification=justification,
        user_approval_level=approval_level,
    )
