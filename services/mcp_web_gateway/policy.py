"""Tool allowlist and OAuth scope enforcement for the MCP web gateway.

Phase 2: read-only tools only.  Write, control, and simulation tools are
blocked at this layer — they never reach the internal MCP server.
"""
from __future__ import annotations

# ── Tool sets ─────────────────────────────────────────────────────────────────

# Exactly the tools exposed through this gateway (Phase 2 read-only subset).
ALLOWED_TOOLS: frozenset[str] = frozenset({
    "get_account_connection_status",
    "get_stream_health",
    "get_account_balances",
    "get_account_positions",
    "get_recent_fills",
    "get_incidents",
    "check_live_trade_policy",
    "get_symbol_snapshot",
    "list_strategies",
    "get_strategy_details",
    "get_recent_executions",
    "get_open_orders",
})

# Tools that exist on the internal server but are explicitly blocked here.
BLOCKED_TOOLS: frozenset[str] = frozenset({
    "request_paper_trade",
    "update_strategy_state",
    "simulate_strategy_on_snapshot",
    "simulate_strategy_on_range",
})

# ── Scope map ─────────────────────────────────────────────────────────────────

# Minimum scope required for each allowed tool.
TOOL_SCOPE_MAP: dict[str, str] = {
    # account observability
    "get_account_connection_status": "mcp:account:read",
    "get_stream_health":             "mcp:account:read",
    "get_account_balances":          "mcp:account:read",
    "get_account_positions":         "mcp:account:read",
    "get_recent_fills":              "mcp:account:read",
    "get_open_orders":               "mcp:account:read",
    "check_live_trade_policy":       "mcp:account:read",
    # general tools
    "get_incidents":                 "mcp:tools:read",
    "get_symbol_snapshot":           "mcp:tools:read",
    # strategy read
    "list_strategies":               "mcp:strategy:read",
    "get_strategy_details":          "mcp:strategy:read",
    "get_recent_executions":         "mcp:strategy:read",
}

ALL_SCOPES: frozenset[str] = frozenset(TOOL_SCOPE_MAP.values())

# ── Policy check ──────────────────────────────────────────────────────────────


class PolicyDenied(Exception):
    """Raised when a tool call is rejected by gateway policy."""
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_tool_call(tool_name: str, granted_scopes: frozenset[str]) -> None:
    """Validate that *tool_name* may be called with *granted_scopes*.

    Raises :class:`PolicyDenied` if:
    - the tool is in the explicit blocklist, or
    - the tool is not in the allowed set, or
    - the token lacks the required scope for the tool.

    Does not raise if everything is satisfied.
    """
    if tool_name in BLOCKED_TOOLS:
        raise PolicyDenied(
            f"tool '{tool_name}' is not exposed by this gateway (write/simulation tools are blocked)"
        )
    if tool_name not in ALLOWED_TOOLS:
        raise PolicyDenied(f"tool '{tool_name}' is not available through this gateway")
    required_scope = TOOL_SCOPE_MAP.get(tool_name)
    if required_scope and required_scope not in granted_scopes:
        raise PolicyDenied(
            f"token missing required scope '{required_scope}' for tool '{tool_name}'"
        )
