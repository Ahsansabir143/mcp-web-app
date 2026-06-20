from pydantic_settings import SettingsConfigDict
from shared.utils.config import BaseServiceSettings


class McpServerSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mcp_api_key: str = "change-me-mcp-key"
    server_name: str = "trading-platform-mcp"
    server_version: str = "0.1.0"
    session_timeout_s: int = 300
    max_result_rows: int = 100
    port: int = 8006


settings = McpServerSettings()
