from __future__ import annotations

VALID_JOB_TRANSITIONS: dict[str, set[str]] = {
    "queued":          {"approved", "blocked", "failed"},
    "approved":        {"submitted", "failed"},
    "blocked":         set(),          # terminal
    "submitted":       {"acknowledged", "failed", "canceled"},
    "acknowledged":    {"partially_filled", "filled", "canceled", "failed"},
    "partially_filled": {"filled", "canceled", "failed"},
    "filled":          set(),          # terminal
    "canceled":        set(),          # terminal
    "failed":          {"rolled_back"},
    "rolled_back":     set(),          # terminal
}

_TERMINAL_STATES: frozenset[str] = frozenset(
    s for s, nexts in VALID_JOB_TRANSITIONS.items() if not nexts
)


class JobLifecycleError(ValueError):
    """Raised when a job state transition is invalid."""


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in VALID_JOB_TRANSITIONS.get(from_state, set())


def assert_transition(from_state: str, to_state: str) -> None:
    """Raise JobLifecycleError if the transition is not allowed."""
    if not can_transition(from_state, to_state):
        raise JobLifecycleError(
            f"Invalid job transition: {from_state!r} → {to_state!r}"
        )


def is_terminal(state: str) -> bool:
    return state in _TERMINAL_STATES
