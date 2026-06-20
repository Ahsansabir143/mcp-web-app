from __future__ import annotations

from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.strategy import StrategyEvaluation
from services.strategy_service.framework.evaluator import StrategyEvaluator


class SimulationRunner:
    """Runs an evaluator over a list of snapshots in simulation mode.

    Does not persist to DB; does not publish to any stream.
    The evaluator's lifecycle state must allow simulation (DRAFT, SIMULATION, or
    any live-publishing state) for signals to be produced.
    """

    def __init__(self, evaluator: StrategyEvaluator) -> None:
        self._evaluator = evaluator

    def run(self, snapshots: list[UnifiedDecisionSnapshot]) -> list[StrategyEvaluation]:
        results = []
        for snapshot in snapshots:
            results.append(self._evaluator.evaluate(snapshot))
        return results


class ReplayRunner:
    """Placeholder for historical snapshot replay from Redis or DB.

    Full implementation deferred to Phase 6.  Exists here to document the
    planned interface and allow import without runtime errors.
    """

    async def replay(
        self,
        strategy_id: object,
        version: int,
        start_ms: int,
        end_ms: int,
    ) -> list[StrategyEvaluation]:
        raise NotImplementedError(
            "Historical replay is deferred to Phase 6. "
            "Use SimulationRunner with pre-loaded snapshots instead."
        )
