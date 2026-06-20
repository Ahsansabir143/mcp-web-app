from pydantic_settings import SettingsConfigDict
from shared.utils.config import BaseServiceSettings


class GatewayApiSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gateway_api_key: str = "change-me-gateway-key"
    admin_api_key: str = "change-me-admin-key"
    rate_limit_requests_per_min: int = 60
    port: int = 8007


settings = GatewayApiSettings()
