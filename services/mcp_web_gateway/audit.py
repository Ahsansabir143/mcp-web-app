"""Structured audit logging for the MCP web gateway.

Every tool call (allowed, denied, or upstream error) emits one JSON log line to
stdout so it can be consumed by any log aggregator (Railway, Datadog, etc.).

Log fields:
    event           always "mcp_tool_call"
    subject         JWT sub claim (user identity)
    client_id       JWT azp / client_id claim
    tool_name       MCP tool name requested
    scope_granted   scope required for this tool
    outcome         "allowed" | "denied" | "upstream_error" | "error"
    duration_ms     wall-clock time for the tool call (float, 1 dp)
    detail          optional human-readable reason (populated on non-allowed outcomes)
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Generator

from pythonjsonlogger import jsonlogger

OUTCOME_ALLOWED = "allowed"
OUTCOME_DENIED = "denied"
OUTCOME_UPSTREAM_ERROR = "upstream_error"
OUTCOME_ERROR = "error"

_audit_logger = logging.getLogger("mcp.gateway.audit")

if not _audit_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    )
    _audit_logger.addHandler(_handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False  # don't double-log through root logger


def log_tool_call(
    *,
    subject: str,
    client_id: str,
    tool_name: str,
    scope_granted: str,
    outcome: str,
    duration_ms: float,
    detail: str = "",
) -> None:
    _audit_logger.info(
        "mcp_tool_call",
        extra={
            "event": "mcp_tool_call",
            "subject": subject,
            "client_id": client_id,
            "tool_name": tool_name,
            "scope_granted": scope_granted,
            "outcome": outcome,
            "duration_ms": round(duration_ms, 1),
            "detail": detail,
        },
    )


@contextmanager
def audit_context(
    subject: str,
    client_id: str,
    tool_name: str,
    scope_granted: str,
) -> Generator[dict, None, None]:
    """Context manager that records outcome and duration automatically.

    The caller sets ``ctx["outcome"]`` (and optionally ``ctx["detail"]``) before
    the block exits.  Duration is measured from entry to exit regardless.

    Usage::

        with audit_context(sub, cid, tool, scope) as ctx:
            ctx["outcome"] = OUTCOME_ALLOWED
            result = do_work()
    """
    ctx: dict = {"outcome": OUTCOME_ERROR, "detail": ""}
    start = time.perf_counter()
    try:
        yield ctx
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        log_tool_call(
            subject=subject,
            client_id=client_id,
            tool_name=tool_name,
            scope_granted=scope_granted,
            outcome=ctx["outcome"],
            duration_ms=duration_ms,
            detail=ctx.get("detail", ""),
        )
