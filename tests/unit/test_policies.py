"""Unit tests for approval and symbol policy logic."""
import pytest

from shared.policies.approval import (
    can_activate_strategy,
    can_execute_live,
    check_approval_level,
    level_rank,
)
from shared.policies.permissions import SymbolPolicy, check_symbol_allowed
from shared.schemas.enums import ApprovalLevel, StrategyState


class TestApprovalLevel:
    def test_level_ordering(self):
        assert level_rank(ApprovalLevel.L0_READONLY) < level_rank(ApprovalLevel.L1_SIMULATION)
        assert level_rank(ApprovalLevel.L1_SIMULATION) < level_rank(ApprovalLevel.L2_PAPER)
        assert level_rank(ApprovalLevel.L2_PAPER) < level_rank(ApprovalLevel.L3_ASSISTED_LIVE)
        assert level_rank(ApprovalLevel.L3_ASSISTED_LIVE) < level_rank(ApprovalLevel.L4_BOUNDED_AUTO)

    def test_same_level_passes(self):
        assert check_approval_level(ApprovalLevel.L2_PAPER, ApprovalLevel.L2_PAPER) is True

    def test_higher_level_passes(self):
        assert check_approval_level(ApprovalLevel.L4_BOUNDED_AUTO, ApprovalLevel.L2_PAPER) is True

    def test_lower_level_fails(self):
        assert check_approval_level(ApprovalLevel.L1_SIMULATION, ApprovalLevel.L2_PAPER) is False

    def test_readonly_cannot_execute_live(self):
        assert can_execute_live(ApprovalLevel.L0_READONLY) is False
        assert can_execute_live(ApprovalLevel.L2_PAPER) is False
        assert can_execute_live(ApprovalLevel.L3_ASSISTED_LIVE) is True
        assert can_execute_live(ApprovalLevel.L4_BOUNDED_AUTO) is True

    def test_strategy_activation_requirements(self):
        assert can_activate_strategy(ApprovalLevel.L0_READONLY, StrategyState.SIMULATION) is False
        assert can_activate_strategy(ApprovalLevel.L1_SIMULATION, StrategyState.SIMULATION) is True
        assert can_activate_strategy(ApprovalLevel.L1_SIMULATION, StrategyState.PAPER_ACTIVE) is False
        assert can_activate_strategy(ApprovalLevel.L2_PAPER, StrategyState.PAPER_ACTIVE) is True
        assert can_activate_strategy(ApprovalLevel.L3_ASSISTED_LIVE, StrategyState.ASSISTED_LIVE) is True
        assert can_activate_strategy(ApprovalLevel.L3_ASSISTED_LIVE, StrategyState.BOUNDED_AUTO_LIVE) is False
        assert can_activate_strategy(ApprovalLevel.L4_BOUNDED_AUTO, StrategyState.BOUNDED_AUTO_LIVE) is True


class TestSymbolPolicy:
    def test_no_restrictions(self):
        p = SymbolPolicy()
        assert p.is_allowed("BTCUSDT") is True
        assert p.is_allowed("ETHUSDT") is True

    def test_allowed_list(self):
        p = SymbolPolicy(allowed_symbols=["BTCUSDT", "ETHUSDT"])
        assert p.is_allowed("BTCUSDT") is True
        assert p.is_allowed("SOLUSDT") is False

    def test_denied_list(self):
        p = SymbolPolicy(denied_symbols=["XRPUSDT"])
        assert p.is_allowed("BTCUSDT") is True
        assert p.is_allowed("XRPUSDT") is False

    def test_denied_takes_priority(self):
        p = SymbolPolicy(allowed_symbols=["BTCUSDT", "XRPUSDT"], denied_symbols=["XRPUSDT"])
        assert p.is_allowed("BTCUSDT") is True
        assert p.is_allowed("XRPUSDT") is False

    def test_wildcard_pattern(self):
        p = SymbolPolicy(allowed_symbols=["BTC*"])
        assert p.is_allowed("BTCUSDT") is True
        assert p.is_allowed("BTCBUSD") is True
        assert p.is_allowed("ETHUSDT") is False

    def test_check_symbol_allowed_helper(self):
        assert check_symbol_allowed("BTCUSDT", None, []) is True
        assert check_symbol_allowed("BTCUSDT", ["BTCUSDT"], []) is True
        assert check_symbol_allowed("ETHUSDT", ["BTCUSDT"], []) is False
        assert check_symbol_allowed("BTCUSDT", None, ["BTCUSDT"]) is False
