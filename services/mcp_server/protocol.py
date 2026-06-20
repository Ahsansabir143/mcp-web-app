"""MCP JSON-RPC protocol constants and response builders.

Implements the MCP 2024-11-05 specification over SSE transport.
"""
from __future__ import annotations

import json

PROTOCOL_VERSION = "2024-11-05"


TOOL_DEFINITIONS = [
    {
        "name": "get_symbol_snapshot",
        "description": (
            "Get the latest market state for a trading symbol. Returns market price, "
            "order book metrics, flow analytics (CVD, delta, RVOL), futures data "
            "(funding rate, OI, liquidation clusters), and technical indicators. "
            "All data is read-only from the analytics hot-state in Redis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair, e.g. BTCUSDT",
                },
                "market_type": {
                    "type": "string",
                    "enum": ["futures", "spot"],
                    "description": "Market type. Default: futures",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "list_strategies",
        "description": (
            "List all strategies with optional filters by symbol and lifecycle state. "
            "Returns id, name, state, market_type, symbol_filters, and current_version."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Filter to strategies that include this symbol",
                },
                "state": {
                    "type": "string",
                    "description": "Filter by lifecycle state (draft, simulation, paper_active, ...)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (1–100). Default: 50",
                },
            },
        },
    },
    {
        "name": "get_strategy_details",
        "description": (
            "Get full detail for a single strategy: definition, current version rules, "
            "parameters, approval level, and the most recent evaluation result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy UUID",
                },
            },
            "required": ["strategy_id"],
        },
    },
    {
        "name": "get_recent_executions",
        "description": (
            "List recent execution jobs with their status, symbol, side, and result. "
            "Filterable by strategy and symbol."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Filter by strategy UUID",
                },
                "symbol": {
                    "type": "string",
                    "description": "Filter by symbol",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows (1–100). Default: 20",
                },
            },
        },
    },
    {
        "name": "get_incidents",
        "description": (
            "List recent execution incidents (orphan fills, stale orders, reconciliation "
            "mismatches). Filterable by symbol and start timestamp."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Filter by symbol in incident context",
                },
                "since_ts": {
                    "type": "integer",
                    "description": "Only incidents after this Unix timestamp in milliseconds",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows (1–200). Default: 50",
                },
            },
        },
    },
    {
        "name": "simulate_strategy_on_snapshot",
        "description": (
            "Dry-run evaluate a strategy against the latest analytics snapshot for a symbol. "
            "Returns signal, direction, confidence, explanation, and a hypothetical TradeIntent "
            "if the signal fires. Nothing is published to the execution stream."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy UUID",
                },
                "symbol": {
                    "type": "string",
                    "description": "Symbol to evaluate against, e.g. BTCUSDT",
                },
                "market_type": {
                    "type": "string",
                    "enum": ["futures", "spot"],
                    "description": "Market type. Default: futures",
                },
            },
            "required": ["strategy_id", "symbol"],
        },
    },
    {
        "name": "simulate_strategy_on_range",
        "description": (
            "Request a range-based strategy back-test. "
            "NOTE: Full historical replay is not yet implemented. "
            "This tool returns a stub response explaining the limitation and "
            "recommends simulate_strategy_on_snapshot instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "symbol": {"type": "string"},
                "start_ms": {"type": "integer"},
                "end_ms": {"type": "integer"},
            },
            "required": ["strategy_id"],
        },
    },
    {
        "name": "request_paper_trade",
        "description": (
            "Submit a paper-mode trade intent for a strategy. The intent is published "
            "to stream:strategy:intents and processed by the execution service which "
            "enforces risk checks, approval levels, and paper-only mode. "
            "The strategy must be in paper_active, assisted_live, or bounded_auto_live state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy UUID",
                },
                "symbol": {
                    "type": "string",
                    "description": "Trading pair, e.g. BTCUSDT",
                },
                "side": {
                    "type": "string",
                    "enum": ["BUY", "SELL"],
                    "description": "Trade direction",
                },
                "size_usd": {
                    "type": "number",
                    "description": "Notional USD size. Current price is read from Redis to derive size.",
                },
                "size": {
                    "type": "number",
                    "description": "Asset size in base units (e.g. BTC quantity). Overrides size_usd.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional rationale for the trade (logged in intent metadata)",
                },
            },
            "required": ["strategy_id", "symbol", "side"],
        },
    },
    {
        "name": "update_strategy_state",
        "description": (
            "Advance or revert a strategy's lifecycle state. "
            "Enforces the strategy state machine (DRAFT→SIMULATION→PAPER_ACTIVE→…) "
            "and approval level requirements. Requires a justification for audit logging."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "Strategy UUID",
                },
                "target_state": {
                    "type": "string",
                    "description": "Target lifecycle state (e.g. simulation, paper_active, paused)",
                },
                "justification": {
                    "type": "string",
                    "description": "Required: reason for the transition (used for audit log)",
                },
                "approval_level": {
                    "type": "string",
                    "description": "Caller approval level (l0_readonly … l4_bounded_auto). Optional; enforces minimum required level for target state.",
                },
            },
            "required": ["strategy_id", "target_state", "justification"],
        },
    },
]

TOOL_MAP: dict[str, dict] = {t["name"]: t for t in TOOL_DEFINITIONS}


# ── JSON-RPC builders ─────────────────────────────────────────────────────────


def ok(id_, result: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result})


def error(id_, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}
    )


def tool_content(data: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(data, default=str)}],
        "isError": False,
    }


def tool_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def initialize_result(server_name: str, server_version: str) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": server_name, "version": server_version},
    }


def tools_list_result() -> dict:
    return {"tools": TOOL_DEFINITIONS}
