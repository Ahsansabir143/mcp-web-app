from pydantic_settings import SettingsConfigDict
from shared.utils.config import BaseServiceSettings


class NormalizerSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    consumer_group: str = "normalizer"
    consumer_name: str = "normalizer-1"
    batch_size: int = 100
    block_ms: int = 1000

    # User UUID used for account hot-state keys.  Empty = skip account hot-state.
    # Resolved properly when multi-account mapping is available (Phase 5+).
    default_account_id: str = ""

    port: int = 8002


settings = NormalizerSettings()
