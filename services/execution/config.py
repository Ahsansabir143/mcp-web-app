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

    # ── Credential encryption ──────────────────────────────────────────────────
    # base64url-encoded 32-byte AES key; required to store/read live credentials
    credential_encryption_key: str = ""

    # ── Live trading gates (all must pass before any real order) ──────────────
    live_trading_enabled: bool = False
    live_trading_account_allowlist: str = ""   # comma-separated account IDs; empty = none allowed
    live_trading_symbol_allowlist: str = "BTCUSDT"   # comma-separated symbols
    live_max_notional_usd: float = 100.0

    # ── User-data stream ──────────────────────────────────────────────────────
    account_stream_enabled: bool = False
    binance_ws_spot_base: str = "wss://stream.binance.com:9443"
    binance_ws_futures_base: str = "wss://fstream.binance.com"
    binance_rest_spot: str = "https://api.binance.com"
    binance_rest_futures: str = "https://fapi.binance.com"
    listen_key_refresh_interval_s: float = 1800.0

    port: int = 8005


settings = ExecutionSettings()
