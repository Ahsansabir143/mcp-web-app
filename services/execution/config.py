from pydantic_settings import SettingsConfigDict
from shared.utils.config import BaseServiceSettings


class ExecutionSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    consumer_group: str = "execution"
    consumer_name: str = "execution-1"
    batch_size: int = 50
    block_ms: int = 500
    job_lock_ttl_s: int = 30
    order_timeout_s: int = 60
    reconcile_interval_s: int = 30
    max_retry_attempts: int = 3
    symbol_cooldown_s: int = 300
    max_position_size_usd: float = 1000.0

    # Default account context used in paper/dev mode
    default_account_id: str = ""
    default_user_id: str = ""
    default_approval_level: str = "l2_paper"

    # Reconciliation
    stale_order_timeout_s: int = 300
    recon_consumer_group: str = "execution-reconcile"
    recon_consumer_name: str = "execution-recon-1"

    port: int = 8005


settings = ExecutionSettings()
