"""MCP caller identity propagated through tool context."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class McpIdentity:
    user_id: str
    client_id: str
    auth_method: str  # "oauth" | "api_key"
    scope: str = ""

    @property
    def is_oauth(self) -> bool:
        return self.auth_method == "oauth"

    def as_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "client_id": self.client_id,
            "auth_method": self.auth_method,
            "scope": self.scope,
        }
