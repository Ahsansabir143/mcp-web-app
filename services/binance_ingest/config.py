from pydantic_settings import SettingsConfigDict

from shared.utils.config import BaseServiceSettings


class BinanceIngestSettings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_futures_api_key: str = ""
    binance_futures_api_secret: str = ""
    binance_use_testnet: bool = False

    binance_ws_spot_base: str = "wss://stream.binance.com:9443"
    binance_ws_futures_base: str = "wss://fstream.binance.com"
    binance_ws_testnet_spot: str = "wss://testnet.binance.vision"
    binance_ws_testnet_futures: str = "wss://stream.binancefuture.com"

    binance_rest_spot: str = "https://api.binance.com"
    binance_rest_futures: str = "https://fapi.binance.com"
    binance_rest_testnet_spot: str = "https://testnet.binance.vision"
    binance_rest_testnet_futures: str = "https://testnet.binancefuture.com"

    reconnect_delay_s: float = 1.0
    reconnect_max_delay_s: float = 60.0
    reconnect_factor: float = 2.0
    heartbeat_interval_s: float = 30.0
    listen_key_refresh_interval_s: float = 1800.0

    # Comma-separated stream names, e.g. "btcusdt@trade,btcusdt@aggTrade"
    spot_streams: str = ""
    futures_streams: str = ""

    port: int = 8001


settings = BinanceIngestSettings()
