from __future__ import annotations

from shared.policies.approval import can_activate_strategy
from shared.schemas.enums import ApprovalLevel, StrategyState


VALID_TRANSITIONS: dict[StrategyState, list[StrategyState]] = {
    StrategyState.DRAFT: [
        StrategyState.SIMULATION,
        StrategyState.ARCHIVED,
    ],
    StrategyState.SIMULATION: [
        StrategyState.PAPER_ACTIVE,
        StrategyState.DRAFT,
        StrategyState.ARCHIVED,
    ],
    StrategyState.PAPER_ACTIVE: [
        StrategyState.ASSISTED_LIVE,
        StrategyState.PAUSED,
        StrategyState.ARCHIVED,
    ],
    StrategyState.ASSISTED_LIVE: [
        StrategyState.BOUNDED_AUTO_LIVE,
        StrategyState.PAUSED,
        StrategyState.ROLLED_BACK,
        StrategyState.ARCHIVED,
    ],
    StrategyState.BOUNDED_AUTO_LIVE: [
        StrategyState.ASSISTED_LIVE,
        StrategyState.PAUSED,
        StrategyState.ROLLED_BACK,
        StrategyState.ARCHIVED,
    ],
    StrategyState.PAUSED: [
        StrategyState.SIMULATION,
        StrategyState.PAPER_ACTIVE,
        StrategyState.ARCHIVED,
    ],
    StrategyState.ROLLED_BACK: [
        StrategyState.SIMULATION,
        StrategyState.ARCHIVED,
    ],
    StrategyState.ARCHIVED: [],   # terminal
}

# States that publish intents to stream:strategy:intents
_EMIT_STATES: frozenset[StrategyState] = frozenset({
    StrategyState.PAPER_ACTIVE,
    StrategyState.ASSISTED_LIVE,
    StrategyState.BOUNDED_AUTO_LIVE,
})

# States that may run evaluation (including simulation-only states)
_SIMULATE_STATES: frozenset[StrategyState] = frozenset({
    StrategyState.DRAFT,
    StrategyState.SIMULATION,
    StrategyState.PAUSED,
})


class LifecycleError(ValueError):
    """Raised when a lifecycle transition is invalid."""


class LifecycleManager:
    """Enforces the strategy state machine and approval checks."""

    def can_transition(self, from_state: StrategyState, to_state: StrategyState) -> bool:
        return to_state in VALID_TRANSITIONS.get(from_state, [])

    def transition(
        self,
        from_state: StrategyState,
        to_state: StrategyState,
        user_approval_level: ApprovalLevel | None = None,
    ) -> StrategyState:
        """Validate and return the next state.

        Raises LifecycleError if the transition is invalid or user lacks approval.
        """
        if not self.can_transition(from_state, to_state):
            raise LifecycleError(
                f"Invalid transition: {from_state.value} → {to_state.value}"
            )
        if user_approval_level is not None:
            if not can_activate_strategy(user_approval_level, to_state):
                raise LifecycleError(
                    f"Approval level '{user_approval_level.value}' insufficient "
                    f"for target state '{to_state.value}'"
                )
        return to_state

    def can_emit_intents(self, state: StrategyState) -> bool:
        """True only for states that publish live intents to the stream."""
        return state in _EMIT_STATES

    def can_simulate(self, state: StrategyState) -> bool:
        """True for states that may run evaluation (simulation or live)."""
        return state in (_SIMULATE_STATES | _EMIT_STATES)

    def is_terminal(self, state: StrategyState) -> bool:
        return state == StrategyState.ARCHIVED
