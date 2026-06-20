from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_consumer = None


def set_consumer(consumer) -> None:
    global _consumer
    _consumer = consumer


@router.get("/health")
async def health():
    return {"status": "ok", "service": "strategy-service"}


@router.get("/health/detail")
async def health_detail():
    if _consumer is None:
        return {"status": "starting", "service": "strategy-service", "strategies": 0}

    registry = _consumer.registry
    evaluators = registry.all_evaluators()

    return {
        "status": "ok",
        "service": "strategy-service",
        "active_strategies": len(evaluators),
        "strategies": [
            {
                "strategy_id": str(ev._strategy_id),
                "version": ev._version,
                "state": ev._state.value,
                "symbol_filters": ev.symbol_filters,
                "can_emit": ev.can_emit_intent(),
            }
            for ev in evaluators
        ],
    }
