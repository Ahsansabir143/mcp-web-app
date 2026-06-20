from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import ApprovalLevel


class McpToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    required_approval: ApprovalLevel = ApprovalLevel.L0_READONLY
    is_destructive: bool = False
    audit_required: bool = True


class McpToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class McpAuditEntry(BaseModel):
    entry_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    session_id: str
    user_id: str
    tool_name: str
    input: dict[str, Any]
    result: McpToolResult
    approval_level: ApprovalLevel
    timestamp_ms: int


class McpPolicyContext(BaseModel):
    user_id: str
    account_id: str | None = None
    approval_level: ApprovalLevel
    environment: str
    trading_mode: str
    allowed_symbols: list[str] | None = None
    denied_symbols: list[str] = Field(default_factory=list)
