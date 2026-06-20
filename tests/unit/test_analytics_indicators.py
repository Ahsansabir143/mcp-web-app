import pytest
from services.analytics.engines.indicators import IndicatorEngine
from services.analytics.engines.funding import compute_funding_pressure


# ── IndicatorEngine ────────────────────────────────────────────────────────────

def _feed(engine: IndicatorEngine, n: int, base_close: float = 100.0, step: float = 0.0):
    for i in range(n):
        c = base_close + i * step
        engine.on_kline_close(
            open_=str(c - 0.5),
            high=str(c + 1.0),
            low=str(c - 1.0),
            close=str(c),
            volume="10",
        )


def test_ema9_seeded_after_9_bars():
    engine = IndicatorEngine()
    _feed(engine, 8)
    assert engine.compute().ema_9 is None   # not yet seeded
    _feed(engine, 1)
    assert engine.compute().ema_9 is not None


def test_ema200_seeded_after_200_bars():
    engine = IndicatorEngine()
    _feed(engine, 199)
    assert engine.compute().ema_200 is None
    _feed(engine, 1)
    assert engine.compute().ema_200 is not None


def test_ema_trending_up():
    engine = IndicatorEngine()
    _feed(engine, 210, base_close=100.0, step=1.0)  # rising prices
    vals = engine.compute()
    assert vals.ema_9 is not None
    assert vals.ema_21 is not None
    # EMA9 > EMA21 in uptrend
    assert vals.ema_9 > vals.ema_21


def test_rsi_seeded_after_15_bars():
    engine = IndicatorEngine()
    _feed(engine, 14)
    assert engine.compute().rsi_14 is None
    _feed(engine, 1)
    assert engine.compute().rsi_14 is not None


def test_rsi_overbought_in_strong_uptrend():
    engine = IndicatorEngine()
    _feed(engine, 30, base_close=100.0, step=2.0)   # consistently rising
    vals = engine.compute()
    assert vals.rsi_14 is not None
    assert vals.rsi_14 > 60.0


def test_rsi_oversold_in_strong_downtrend():
    engine = IndicatorEngine()
    _feed(engine, 30, base_close=1000.0, step=-10.0)  # falling
    vals = engine.compute()
    assert vals.rsi_14 is not None
    assert vals.rsi_14 < 40.0


def test_rsi_flat_market():
    engine = IndicatorEngine()
    _feed(engine, 30, base_close=100.0, step=0.0)
    vals = engine.compute()
    assert vals.rsi_14 is not None
    # All gains/losses zero → RSI undefined; our impl returns 100 (no losses)
    # or leaves it as 100 when avg_loss=0
    # Just verify it is computable and in valid range
    assert 0.0 <= vals.rsi_14 <= 100.0


def test_vwap_increases_with_volume():
    engine = IndicatorEngine()
    engine.on_kline_close("99", "101", "99", "100", "10")
    engine.on_kline_close("101", "103", "101", "102", "20")
    vals = engine.compute()
    assert vals.vwap is not None
    # VWAP = (100×10 + 102×20) / 30 ≈ 101.33 (using typical prices)
    assert 100.0 < vals.vwap < 103.0


def test_vwap_reset():
    engine = IndicatorEngine()
    _feed(engine, 5, base_close=100.0)
    engine.reset_vwap()
    _feed(engine, 3, base_close=200.0)
    vals = engine.compute()
    # After reset, VWAP reflects only the 3 new bars
    assert vals.vwap is not None
    assert vals.vwap > 100.0


def test_bollinger_bands_seeded_after_20_bars():
    engine = IndicatorEngine()
    _feed(engine, 19)
    vals = engine.compute()
    assert vals.bb_upper is None
    assert vals.bb_middle is None
    _feed(engine, 1)
    vals = engine.compute()
    assert vals.bb_upper is not None
    assert vals.bb_middle is not None
    assert vals.bb_lower is not None


def test_bollinger_bands_ordering():
    engine = IndicatorEngine()
    _feed(engine, 25, base_close=100.0, step=0.1)
    vals = engine.compute()
    assert vals.bb_upper > vals.bb_middle > vals.bb_lower


def test_bollinger_width_positive():
    engine = IndicatorEngine()
    _feed(engine, 25, base_close=100.0, step=0.5)
    vals = engine.compute()
    assert vals.bb_width is not None
    assert vals.bb_width > 0


def test_atr_seeded_after_14_bars():
    engine = IndicatorEngine()
    _feed(engine, 13)
    assert engine.compute().atr_14 is None
    _feed(engine, 1)
    assert engine.compute().atr_14 is not None


def test_atr_positive():
    engine = IndicatorEngine()
    _feed(engine, 20, base_close=100.0, step=0.2)
    vals = engine.compute()
    assert vals.atr_14 is not None
    assert vals.atr_14 > 0.0


def test_invalid_kline_ignored():
    engine = IndicatorEngine()
    engine.on_kline_close("x", "y", "z", "bad", "nope")
    _feed(engine, 8)  # 8 valid bars — invalid bar was dropped so total = 8
    vals = engine.compute()
    # EMA9 needs 9 bars; only 8 valid → should not be seeded yet
    assert vals.ema_9 is None


# ── Funding pressure ───────────────────────────────────────────────────────────

def test_funding_pressure_zero_rate():
    fp = compute_funding_pressure("0.0", "30000.0", "30000.0")
    assert fp == 0.0


def test_funding_pressure_positive_rate():
    fp = compute_funding_pressure("0.0001", "30000.0", "30000.0")
    assert fp is not None
    assert fp > 0.0  # longs pay → positive pressure


def test_funding_pressure_negative_rate():
    fp = compute_funding_pressure("-0.0001", "30000.0", "30000.0")
    assert fp is not None
    assert fp < 0.0  # shorts pay → negative pressure


def test_funding_pressure_mark_above_index():
    # mark > index: premium → adds to bearish pressure
    fp_neutral = compute_funding_pressure("0.0", "30000.0", "30000.0")
    fp_premium = compute_funding_pressure("0.0", "30100.0", "30000.0")
    assert fp_premium > fp_neutral


def test_funding_pressure_invalid_inputs():
    assert compute_funding_pressure("bad", "30000", "30000") is None
    assert compute_funding_pressure("0.0001", "bad", "30000") is None
    assert compute_funding_pressure("0.0001", "30000", "0") == pytest.approx(0.0001 * 10000, rel=0.01)
