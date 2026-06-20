from pydantic_settings import SettingsConfigDict
from shared.utils.config import BaseServiceSettings


class StrategyServiceSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    consumer_group: str = "strategy"
    consumer_name: str = "strategy-1"
    batch_size: int = 50
    block_ms: int = 500
    max_intents_per_cycle: int = 10
    strategy_reload_interval_s: float = 30.0
    allow_simulation_without_account: bool = True
    port: int = 8004


settings = StrategyServiceSettings()
