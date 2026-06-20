"""Tests for LifecycleManager: state transitions, emission rules, approval checks."""
import pytest

from shared.schemas.enums import ApprovalLevel, StrategyState
from services.strategy_service.lifecycle.transitions import (
    VALID_TRANSITIONS,
    LifecycleError,
    LifecycleManager,
)


@pytest.fixture
def lm() -> LifecycleManager:
    return LifecycleManager()


# ── Valid transitions ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("from_state,to_state", [
    (StrategyState.DRAFT, StrategyState.SIMULATION),
    (StrategyState.DRAFT, StrategyState.ARCHIVED),
    (StrategyState.SIMULATION, StrategyState.PAPER_ACTIVE),
    (StrategyState.SIMULATION, StrategyState.DRAFT),
    (StrategyState.SIMULATION, StrategyState.ARCHIVED),
    (StrategyState.PAPER_ACTIVE, StrategyState.ASSISTED_LIVE),
    (StrategyState.PAPER_ACTIVE, StrategyState.PAUSED),
    (StrategyState.PAPER_ACTIVE, StrategyState.ARCHIVED),
    (StrategyState.ASSISTED_LIVE, StrategyState.BOUNDED_AUTO_LIVE),
    (StrategyState.ASSISTED_LIVE, StrategyState.PAUSED),
    (StrategyState.ASSISTED_LIVE, StrategyState.ROLLED_BACK),
    (StrategyState.ASSISTED_LIVE, StrategyState.ARCHIVED),
    (StrategyState.BOUNDED_AUTO_LIVE, StrategyState.ASSISTED_LIVE),
    (StrategyState.BOUNDED_AUTO_LIVE, StrategyState.PAUSED),
    (StrategyState.BOUNDED_AUTO_LIVE, StrategyState.ROLLED_BACK),
    (StrategyState.BOUNDED_AUTO_LIVE, StrategyState.ARCHIVED),
    (StrategyState.PAUSED, StrategyState.SIMULATION),
    (StrategyState.PAUSED, StrategyState.PAPER_ACTIVE),
    (StrategyState.PAUSED, StrategyState.ARCHIVED),
    (StrategyState.ROLLED_BACK, StrategyState.SIMULATION),
    (StrategyState.ROLLED_BACK, StrategyState.ARCHIVED),
])
def test_valid_transition(lm, from_state, to_state):
    assert lm.can_transition(from_state, to_state) is True
    result = lm.transition(from_state, to_state)
    assert result == to_state


# ── Invalid transitions ───────────────────────────────────────────────────────

@pytest.mark.parametrize("from_state,to_state", [
    (StrategyState.DRAFT, StrategyState.PAPER_ACTIVE),       # must go through SIMULATION first
    (StrategyState.DRAFT, StrategyState.ASSISTED_LIVE),
    (StrategyState.SIMULATION, StrategyState.BOUNDED_AUTO_LIVE),
    (StrategyState.ARCHIVED, StrategyState.DRAFT),            # terminal
    (StrategyState.ARCHIVED, StrategyState.SIMULATION),
    (StrategyState.ARCHIVED, StrategyState.PAPER_ACTIVE),
    (StrategyState.ROLLED_BACK, StrategyState.PAPER_ACTIVE),  # must re-simulate first
    (StrategyState.PAPER_ACTIVE, StrategyState.DRAFT),
    (StrategyState.PAPER_ACTIVE, StrategyState.BOUNDED_AUTO_LIVE),  # must pass ASSISTED_LIVE
])
def test_invalid_transition_raises(lm, from_state, to_state):
    assert lm.can_transition(from_state, to_state) is False
    with pytest.raises(LifecycleError):
        lm.transition(from_state, to_state)


def test_archived_is_terminal(lm):
    assert lm.is_terminal(StrategyState.ARCHIVED) is True
    assert VALID_TRANSITIONS[StrategyState.ARCHIVED] == []


def test_non_terminal_states_not_terminal(lm):
    for state in StrategyState:
        if state != StrategyState.ARCHIVED:
            assert lm.is_terminal(state) is False


# ── Approval checks ───────────────────────────────────────────────────────────

def test_transition_with_sufficient_approval(lm):
    result = lm.transition(
        StrategyState.DRAFT,
        StrategyState.SIMULATION,
        user_approval_level=ApprovalLevel.L1_SIMULATION,
    )
    assert result == StrategyState.SIMULATION


def test_transition_simulation_to_paper_requires_l2(lm):
    with pytest.raises(LifecycleError, match="insufficient"):
        lm.transition(
            StrategyState.SIMULATION,
            StrategyState.PAPER_ACTIVE,
            user_approval_level=ApprovalLevel.L1_SIMULATION,
        )


def test_transition_simulation_to_paper_with_l2_ok(lm):
    result = lm.transition(
        StrategyState.SIMULATION,
        StrategyState.PAPER_ACTIVE,
        user_approval_level=ApprovalLevel.L2_PAPER,
    )
    assert result == StrategyState.PAPER_ACTIVE


def test_transition_to_assisted_live_requires_l3(lm):
    with pytest.raises(LifecycleError):
        lm.transition(
            StrategyState.PAPER_ACTIVE,
            StrategyState.ASSISTED_LIVE,
            user_approval_level=ApprovalLevel.L2_PAPER,
        )


def test_transition_to_bounded_auto_requires_l4(lm):
    with pytest.raises(LifecycleError):
        lm.transition(
            StrategyState.ASSISTED_LIVE,
            StrategyState.BOUNDED_AUTO_LIVE,
            user_approval_level=ApprovalLevel.L3_ASSISTED_LIVE,
        )


def test_l4_can_do_all_transitions(lm):
    # L4 satisfies all approval checks
    lm.transition(StrategyState.DRAFT, StrategyState.SIMULATION, ApprovalLevel.L4_BOUNDED_AUTO)
    lm.transition(StrategyState.SIMULATION, StrategyState.PAPER_ACTIVE, ApprovalLevel.L4_BOUNDED_AUTO)
    lm.transition(StrategyState.PAPER_ACTIVE, StrategyState.ASSISTED_LIVE, ApprovalLevel.L4_BOUNDED_AUTO)
    lm.transition(StrategyState.ASSISTED_LIVE, StrategyState.BOUNDED_AUTO_LIVE, ApprovalLevel.L4_BOUNDED_AUTO)


# ── Emission rules ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state,expected", [
    (StrategyState.PAPER_ACTIVE, True),
    (StrategyState.ASSISTED_LIVE, True),
    (StrategyState.BOUNDED_AUTO_LIVE, True),
    (StrategyState.DRAFT, False),
    (StrategyState.SIMULATION, False),
    (StrategyState.PAUSED, False),
    (StrategyState.ROLLED_BACK, False),
    (StrategyState.ARCHIVED, False),
])
def test_can_emit_intents(lm, state, expected):
    assert lm.can_emit_intents(state) is expected


# ── Simulation rules ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("state,expected", [
    (StrategyState.DRAFT, True),
    (StrategyState.SIMULATION, True),
    (StrategyState.PAUSED, True),
    (StrategyState.PAPER_ACTIVE, True),
    (StrategyState.ASSISTED_LIVE, True),
    (StrategyState.BOUNDED_AUTO_LIVE, True),
    (StrategyState.ROLLED_BACK, False),
    (StrategyState.ARCHIVED, False),
])
def test_can_simulate(lm, state, expected):
    assert lm.can_simulate(state) is expected
