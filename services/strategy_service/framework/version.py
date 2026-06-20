from __future__ import annotations

from shared.policies.approval import can_activate_strategy, check_approval_level, level_rank
from shared.schemas.enums import ApprovalLevel, StrategyState
from shared.schemas.strategy import StrategyVersion


class VersionResolver:
    """Selects the right StrategyVersion and checks whether it may be activated."""

    def resolve(
        self,
        versions: list[StrategyVersion],
        target_version: int,
    ) -> StrategyVersion | None:
        """Return the StrategyVersion matching target_version, or None."""
        for v in versions:
            if v.version == target_version:
                return v
        return None

    def can_activate(
        self,
        version: StrategyVersion,
        target_state: StrategyState,
        user_approval_level: ApprovalLevel,
    ) -> tuple[bool, str]:
        """Check whether a user with user_approval_level may activate the version in target_state.

        Two checks must pass:
        1. The user level meets the state-level requirement.
        2. The user level meets the version's own approval_required.

        Returns (True, "ok") or (False, reason).
        """
        # Check state requirement
        if not can_activate_strategy(user_approval_level, target_state):
            return False, (
                f"user level '{user_approval_level.value}' insufficient "
                f"for state '{target_state.value}'"
            )

        # Check version-level requirement
        required = version.approval_required
        if isinstance(required, str):
            required = ApprovalLevel(required)
        if not check_approval_level(user_approval_level, required):
            return False, (
                f"user level '{user_approval_level.value}' insufficient "
                f"for version requiring '{required.value}'"
            )

        return True, "ok"
