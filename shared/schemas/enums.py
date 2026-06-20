from enum import Enum


class EventType(str, Enum):
    TRADE = "trade"
    AGG_TRADE = "agg_trade"
    BOOK_TICKER = "book_ticker"
    ORDERBOOK_SNAPSHOT = "orderbook_snapshot"
    ORDERBOOK_DELTA = "orderbook_delta"
    KLINE = "kline"
    CONTINUOUS_KLINE = "continuous_kline"
    MINI_TICKER = "mini_ticker"
    TICKER_24H = "ticker_24h"
    ROLLING_TICKER = "rolling_ticker"
    MARK_PRICE = "mark_price"
    ALL_MARKET_MARK_PRICE = "all_market_mark_price"
    LIQUIDATION = "liquidation"
    CONTRACT_INFO = "contract_info"
    COMPOSITE_INDEX = "composite_index"
    ASSET_INDEX = "asset_index"
    OPEN_INTEREST = "open_interest"
    USER_ORDER = "user_order"
    USER_BALANCE = "user_balance"
    USER_POSITION = "user_position"
    ACCOUNT_UPDATE = "account_update"
    EXECUTION_EVENT = "execution_event"
    STRATEGY_INTENT = "strategy_intent"
    VENUE_STATUS = "venue_status"


class Venue(str, Enum):
    BINANCE = "binance"


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class ApprovalLevel(str, Enum):
    L0_READONLY = "l0_readonly"
    L1_SIMULATION = "l1_simulation"
    L2_PAPER = "l2_paper"
    L3_ASSISTED_LIVE = "l3_assisted_live"
    L4_BOUNDED_AUTO = "l4_bounded_auto"


class EnvironmentMode(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"
    LIMIT_MAKER = "LIMIT_MAKER"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PENDING_CANCEL = "PENDING_CANCEL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"
    GTD = "GTD"


class StrategyState(str, Enum):
    DRAFT = "draft"
    SIMULATION = "simulation"
    PAPER_ACTIVE = "paper_active"
    ASSISTED_LIVE = "assisted_live"
    BOUNDED_AUTO_LIVE = "bounded_auto_live"
    PAUSED = "paused"
    ROLLED_BACK = "rolled_back"
    ARCHIVED = "archived"


class ExecutionJobStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    RISK_CHECK = "risk_check"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    FAILED = "failed"
    RECONCILING = "reconciling"
    COMPLETE = "complete"


class IncidentSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RiskCheckName(str, Enum):
    MAX_POSITION_SIZE = "max_position_size"
    MAX_LEVERAGE = "max_leverage"
    MAX_DAILY_LOSS = "max_daily_loss"
    MAX_CONCURRENT_POSITIONS = "max_concurrent_positions"
    SYMBOL_COOLDOWN = "symbol_cooldown"
    FUNDING_WINDOW = "funding_window"
    CIRCUIT_BREAKER = "circuit_breaker"
    KILL_SWITCH = "kill_switch"
    USER_PAUSE = "user_pause"
    SYMBOL_PAUSE = "symbol_pause"
    APPROVAL_LEVEL = "approval_level"
    SYMBOL_POLICY = "symbol_policy"
    ACCOUNT_MODE = "account_mode"
