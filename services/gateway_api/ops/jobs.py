"""Ops: execution job inspection.

GET /api/ops/execution/jobs — full execution job view filterable by
status, symbol, and strategy_id. Richer than the MCP-facing
/api/executions/recent endpoint (includes intent JSON, error detail, etc.).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, select

from services.gateway_api.ops.auth import verify_admin_api_key
from shared.db.models.execution import ExecutionJob as ExecutionJobModel

router = APIRouter(dependencies=[Depends(verify_admin_api_key)])


@router.get("/api/ops/execution/jobs")
async def list_jobs(
    request: Request,
    status: str | None = Query(default=None, description="Filter by job status"),
    symbol: str | None = Query(default=None, description="Filter by symbol"),
    strategy_id: str | None = Query(default=None, description="Filter by strategy UUID"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List execution jobs with operator-level detail.

    Unlike /api/executions/recent, this includes full intent_json, error
    text, trading_mode, and all status values (including pending/failed).
    Intended for ops debugging, not end-user display.
    """
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        stmt = (
            select(ExecutionJobModel)
            .order_by(desc(ExecutionJobModel.created_at))
            .limit(limit)
        )

        if status:
            stmt = stmt.where(ExecutionJobModel.status == status)
        if symbol:
            stmt = stmt.where(ExecutionJobModel.symbol == symbol)
        if strategy_id:
            try:
                sid = uuid.UUID(strategy_id)
                stmt = stmt.where(ExecutionJobModel.strategy_id == sid)
            except ValueError:
                pass

        result = await session.execute(stmt)
        jobs = result.scalars().all()

        rows = []
        for job in jobs:
            rows.append({
                "job_id": str(job.id),
                "status": job.status,
                "symbol": job.symbol,
                "side": job.side,
                "market_type": job.market_type,
                "trading_mode": job.trading_mode,
                "strategy_id": str(job.strategy_id) if job.strategy_id else None,
                "account_id": str(job.account_id),
                "client_order_id": job.deterministic_client_order_id,
                "result": job.result_json,
                "error": job.error,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            })

    return {"jobs": rows, "count": len(rows), "filters": {
        "status": status, "symbol": symbol, "strategy_id": strategy_id,
    }}
