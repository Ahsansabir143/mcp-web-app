"""Strategy factory — dispatch on ``strategy_type`` parameter."""
from __future__ import annotations

import uuid
from typing import Any

from services.strategy_service.framework.base import BaseStrategy
from services.strategy_service.framework.rule_adapter import RuleBasedStrategy


def build_strategy(
    strategy_id: uuid.UUID,
    version: int,
    rules: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> BaseStrategy:
    """Instantiate the correct strategy subclass for the given parameters.

    Dispatch key: ``parameters["strategy_type"]`` (default: ``"rule_based"``).

    New strategy types must be added here. Callers never import concrete
    subclasses directly — they always go through this factory.
    """
    strategy_type = str(parameters.get("strategy_type", "rule_based")).lower()

    if strategy_type == "ma_crossover":
        from services.strategy_service.strategies.ma_crossover import MACrossoverStrategy

        return MACrossoverStrategy(
            strategy_id=strategy_id,
            version=version,
            parameters=parameters,
            rules=rules,
        )

    # Default — rule_based (also catches any unknown type gracefully)
    return RuleBasedStrategy(
        strategy_id=strategy_id,
        version=version,
        rules=rules,
        parameters=parameters,
    )
