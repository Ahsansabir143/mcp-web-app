from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.schemas.enums import EnvironmentMode, TradingMode


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: EnvironmentMode = EnvironmentMode.DEV
    trading_mode: TradingMode = TradingMode.PAPER
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/trading_platform"
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_max_len: int = 50_000

    secret_key: str = "change-me"
