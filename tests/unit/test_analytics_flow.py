import pytest
from services.analytics.engines.flow import FlowEngine, TradeRecord
from services.analytics.engines.rvol import RvolCalculator
from services.analytics.engines.liquidations import LiquidationClusterDetector


# ── TradeRecord ────────────────────────────────────────────────────────────────

def test_trade_record_taker_buy():
    t = TradeRecord(price=100.0, qty=1.0, is_buyer_maker=False, timestamp_ms=1000)
    assert t.delta == 1.0


def test_trade_record_taker_sell():
    t = TradeRecord(price=100.0, qty=1.0, is_buyer_maker=True, timestamp_ms=1000)
    assert t.delta == -1.0


# ── FlowEngine ─────────────────────────────────────────────────────────────────

def _flow_with_trades(trades):
    """Helper: create FlowEngine and feed a list of (price, qty, buyer_maker, ts)."""
    fe = FlowEngine()
    for price, qty, bm, ts in trades:
        fe.on_trade(str(price), str(qty), bm, ts)
    return fe


def test_cvd_accumulates():
    fe = FlowEngine()
    fe.on_trade("100", "2", False, 1000)  # taker buy: +2
    fe.on_trade("100", "1", True, 2000)   # taker sell: -1
    assert abs(fe.cvd - 1.0) < 1e-9


def test_delta_windowed():
    fe = FlowEngine()
    for i in range(100):
        fe.on_trade("100", "1", False, i * 100)   # 100 taker buys
    fe.on_trade("100", "3", True, 10100)           # 1 taker sell at end
    delta = fe.compute_delta(window=10)            # last 10 trades
    # 9 buys + 1 sell = +9 - 3 = +6
    assert abs(delta - 6.0) < 1e-9


def test_aggression_ratio_all_buys():
    fe = FlowEngine(aggression_window=10)
    for i in range(10):
        fe.on_trade("100", "1", False, i * 100)
    ratio = fe.compute_aggression_ratio()
    assert ratio == 1.0


def test_aggression_ratio_half():
    fe = FlowEngine(aggression_window=10)
    for i in range(5):
        fe.on_trade("100", "1", False, i * 100)
    for i in range(5, 10):
        fe.on_trade("100", "1", True, i * 100)
    ratio = fe.compute_aggression_ratio()
    assert abs(ratio - 0.5) < 1e-9


def test_aggression_ratio_empty():
    fe = FlowEngine()
    assert fe.compute_aggression_ratio() is None


def test_tape_speed_basic():
    fe = FlowEngine(tape_speed_window_s=10.0)
    # 20 trades spread across 20 seconds — only last 10s count
    now_ms = 20_000
    for i in range(20):
        fe.on_trade("100", "1", False, i * 1000)  # t=0..19s
    # In last 10s (10000..20000 ms), there are trades at 10,11,...,19s = 10 trades
    speed = fe.compute_tape_speed_per_min(now_ms)
    # 10 trades in 10s = 60 per min
    assert abs(speed - 60.0) < 0.1


def test_buy_sell_volumes():
    fe = FlowEngine()
    fe.on_trade("100", "3", False, 1000)   # buy vol
    fe.on_trade("100", "2", True, 2000)    # sell vol
    buy, sell = fe.compute_buy_sell_volumes(window=100)
    assert abs(buy - 3.0) < 1e-9
    assert abs(sell - 2.0) < 1e-9


def test_large_trade_stats():
    fe = FlowEngine()
    fe.on_trade("100", "200", False, 1000)   # buy $20k
    fe.on_trade("100", "10", True, 2000)     # sell $1k (small)
    fe.on_trade("100", "150", True, 3000)    # sell $15k
    large_buys, large_sells = fe.compute_large_trade_stats(100.0, 10_000.0, window=100)
    assert large_buys == 1
    assert large_sells == 1


def test_cvd_slope_positive():
    fe = FlowEngine()
    for i in range(20):
        fe.on_trade("100", str(i + 1), False, i * 100)  # increasing buys
    slope = fe.cvd_slope(window=20)
    assert slope is not None
    assert slope > 0


def test_cvd_slope_negative():
    fe = FlowEngine()
    for i in range(20):
        fe.on_trade("100", str(i + 1), True, i * 100)   # increasing sells
    slope = fe.cvd_slope(window=20)
    assert slope is not None
    assert slope < 0


def test_cvd_slope_too_few_trades():
    fe = FlowEngine()
    fe.on_trade("100", "1", False, 1000)
    assert fe.cvd_slope(window=20) is None


def test_reset_cvd():
    fe = FlowEngine()
    fe.on_trade("100", "5", False, 1000)
    fe.reset_cvd()
    assert fe.cvd == 0.0


def test_invalid_input_ignored():
    fe = FlowEngine()
    fe.on_trade("not_a_number", "1", False, 1000)
    assert fe.cvd == 0.0


# ── RvolCalculator ─────────────────────────────────────────────────────────────

def test_rvol_no_history():
    rv = RvolCalculator(bucket_duration_s=60.0, lookback_buckets=5)
    rv.on_trade("100", 1000)
    assert rv.compute() is None


def test_rvol_above_one():
    rv = RvolCalculator(bucket_duration_s=5.0, lookback_buckets=4)
    ts = 0
    for _ in range(4):
        rv.on_trade("1", ts)
        ts += 5001
    # Now feed extra volume in current bucket
    for _ in range(10):
        rv.on_trade("1", ts + 100)
    result = rv.compute()
    assert result is not None
    assert result > 1.0


def test_rvol_below_one():
    rv = RvolCalculator(bucket_duration_s=5.0, lookback_buckets=4)
    ts = 0
    for _ in range(4):
        for _ in range(100):
            rv.on_trade("1", ts)
        ts += 5001
    # tiny volume in current bucket
    rv.on_trade("0.01", ts + 100)
    result = rv.compute()
    assert result is not None
    assert result < 1.0


# ── LiquidationClusterDetector ─────────────────────────────────────────────────

def test_liquidation_totals_empty():
    lcd = LiquidationClusterDetector()
    long_usd, short_usd = lcd.compute_cluster_totals(50_000.0, now_ms=60_000)
    assert long_usd == 0.0
    assert short_usd == 0.0


def test_liquidation_totals_long():
    lcd = LiquidationClusterDetector(window_s=60.0)
    lcd.on_liquidation("SELL", "1.0", "50000", timestamp_ms=30_000)
    long_usd, short_usd = lcd.compute_cluster_totals(50_000.0, now_ms=60_000)
    assert abs(long_usd - 50_000.0) < 0.01
    assert short_usd == 0.0


def test_liquidation_totals_short():
    lcd = LiquidationClusterDetector(window_s=60.0)
    lcd.on_liquidation("BUY", "1.0", "50000", timestamp_ms=30_000)
    long_usd, short_usd = lcd.compute_cluster_totals(50_000.0, now_ms=60_000)
    assert long_usd == 0.0
    assert abs(short_usd - 50_000.0) < 0.01


def test_liquidation_outside_window_excluded():
    lcd = LiquidationClusterDetector(window_s=30.0)
    lcd.on_liquidation("SELL", "2.0", "50000", timestamp_ms=1_000)   # old
    lcd.on_liquidation("SELL", "1.0", "50000", timestamp_ms=40_000)  # in window
    long_usd, short_usd = lcd.compute_cluster_totals(50_000.0, now_ms=60_000)
    # Only the recent one (40s) is within 30s window of now (60s)
    assert abs(long_usd - 50_000.0) < 0.01


def test_liquidation_clusters_grouped():
    lcd = LiquidationClusterDetector(window_s=60.0, bucket_pct=1.0, min_cluster_count=2)
    # Three liquidations near the same price
    for _ in range(3):
        lcd.on_liquidation("SELL", "1.0", "50000", timestamp_ms=30_000)
    clusters = lcd.compute_clusters(50_000.0, now_ms=60_000)
    assert len(clusters) > 0
    assert clusters[0].count == 3
