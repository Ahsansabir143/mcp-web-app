from pydantic_settings import SettingsConfigDict
from shared.utils.config import BaseServiceSettings


class AnalyticsSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    consumer_group: str = "analytics"
    consumer_name: str = "analytics-1"
    batch_size: int = 200
    block_ms: int = 500
    snapshot_publish_interval_s: float = 1.0
    cvd_window_trades: int = 1000
    wall_min_notional_usd: float = 100_000.0
    wall_depth_levels: int = 10
    rvol_lookback_candles: int = 20
    tape_speed_window_s: float = 10.0
    port: int = 8003
    default_account_id: str = ""


settings = AnalyticsSettings()
