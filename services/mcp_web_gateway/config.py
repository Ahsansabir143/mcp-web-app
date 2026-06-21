from __future__ import annotations

import logging

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from shared.schemas.enums import EnvironmentMode
from shared.utils.config import BaseServiceSettings

logger = logging.getLogger(__name__)


class McpWebGatewaySettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── OAuth 2.1 resource-server validation ─────────────────────────────────
    # Production path: set OAUTH_ISSUER_URL (JWKS derived) or OAUTH_JWKS_URL.
    # Dev/test path: set OAUTH_JWT_SECRET for HS256 static-secret validation.
    oauth_issuer_url: str = ""               # JWT iss; also used to derive JWKS URL
    oauth_audience: str = "mcp-web-gateway"  # JWT aud claim to validate
    oauth_jwks_url: str = ""                 # Explicit JWKS URL (overrides issuer derivation)
    oauth_jwt_secret: str = ""               # HS256 fallback — NEVER use in production

    # ── Internal MCP server ───────────────────────────────────────────────────
    mcp_internal_url: str = "http://mcp-server:8006"
    mcp_internal_api_key: str = ""

    # ── Public gateway metadata (used in /.well-known/oauth-protected-resource)
    mcp_resource_url: str = "http://localhost:8007"
    mcp_authorization_servers: str = ""      # comma-separated OAuth server base URLs

    port: int = 8007
    session_timeout_s: int = 300             # max client SSE session lifetime (seconds)
    max_sessions: int = 50                   # concurrent session cap (anti-DoS)

    @model_validator(mode="after")
    def _validate_oauth_config(self) -> "McpWebGatewaySettings":
        is_prod_like = self.environment in (EnvironmentMode.STAGING, EnvironmentMode.PROD)
        if self.oauth_jwt_secret and is_prod_like:
            logger.warning(
                "OAUTH_JWT_SECRET is set in environment=%s. "
                "This is insecure — use OAUTH_JWKS_URL in staging/production.",
                self.environment,
            )
        if not any([self.oauth_jwks_url, self.oauth_issuer_url, self.oauth_jwt_secret]):
            logger.warning(
                "No OAuth validator configured for mcp-web-gateway. "
                "Set OAUTH_JWKS_URL (production) or OAUTH_JWT_SECRET (dev)."
            )
        return self


settings = McpWebGatewaySettings()
