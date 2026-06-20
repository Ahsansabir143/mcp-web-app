from shared.schemas.enums import ApprovalLevel, StrategyState


_LEVEL_ORDER = [
    ApprovalLevel.L0_READONLY,
    ApprovalLevel.L1_SIMULATION,
    ApprovalLevel.L2_PAPER,
    ApprovalLevel.L3_ASSISTED_LIVE,
    ApprovalLevel.L4_BOUNDED_AUTO,
]


def level_rank(level: ApprovalLevel) -> int:
    return _LEVEL_ORDER.index(level)


def check_approval_level(user_level: ApprovalLevel, required_level: ApprovalLevel) -> bool:
    """Return True if the user's approval level meets or exceeds the required level."""
    return level_rank(user_level) >= level_rank(required_level)


def can_execute_live(user_level: ApprovalLevel) -> bool:
    return check_approval_level(user_level, ApprovalLevel.L3_ASSISTED_LIVE)


def can_activate_strategy(user_level: ApprovalLevel, target_state: StrategyState) -> bool:
    state_requirements = {
        StrategyState.SIMULATION: ApprovalLevel.L1_SIMULATION,
        StrategyState.PAPER_ACTIVE: ApprovalLevel.L2_PAPER,
        StrategyState.ASSISTED_LIVE: ApprovalLevel.L3_ASSISTED_LIVE,
        StrategyState.BOUNDED_AUTO_LIVE: ApprovalLevel.L4_BOUNDED_AUTO,
    }
    required = state_requirements.get(target_state, ApprovalLevel.L4_BOUNDED_AUTO)
    return check_approval_level(user_level, required)
