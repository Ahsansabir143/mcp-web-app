import pytest
from services.analytics.engines.book_analytics import (
    WallDetector,
    compute_imbalance,
    compute_spread,
)


# ── Spread ─────────────────────────────────────────────────────────────────────

def test_spread_basic():
    r = compute_spread("100.0", "100.5", "1.0", "1.0")
    assert r is not None
    assert abs(r.spread - 0.5) < 1e-9
    assert abs(r.mid_price - 100.25) < 1e-9
    assert abs(r.spread_bps - 50 / 100.25 * 100) < 0.01


def test_spread_invalid_prices():
    assert compute_spread("0", "100", "1", "1") is None
    assert compute_spread("100", "0", "1", "1") is None
    assert compute_spread("bad", "100", "1", "1") is None


def test_spread_ask_less_than_bid():
    assert compute_spread("100.5", "100.0", "1", "1") is None


def test_spread_equal_prices():
    assert compute_spread("100", "100", "1", "1") is None


# ── Imbalance ──────────────────────────────────────────────────────────────────

def test_imbalance_bid_heavy():
    bids = [("100", "10")] * 5
    asks = [("101", "2")] * 5
    r = compute_imbalance(bids, asks, price=100.0)
    assert r is not None
    assert r.imbalance_ratio > 0


def test_imbalance_ask_heavy():
    bids = [("100", "1")] * 5
    asks = [("101", "10")] * 5
    r = compute_imbalance(bids, asks, price=100.0)
    assert r is not None
    assert r.imbalance_ratio < 0


def test_imbalance_balanced():
    bids = [("100", "5")] * 5
    asks = [("101", "5")] * 5
    r = compute_imbalance(bids, asks, price=100.0)
    assert r is not None
    assert abs(r.imbalance_ratio) < 1e-9


def test_imbalance_empty():
    assert compute_imbalance([], [], price=100.0) is None


def test_imbalance_depth_limit():
    bids = [("100", "100")] * 20  # 20 levels but top_n=10
    asks = [("101", "1")] * 20
    r = compute_imbalance(bids, asks, price=100.0, top_n=10)
    assert r is not None
    # 10 bid levels × 100 vs 10 ask levels × 1 → very bid-heavy
    assert r.imbalance_ratio > 0.8


# ── WallDetector ───────────────────────────────────────────────────────────────

def _make_bids(price_qty_pairs):
    return [(str(p), str(q)) for p, q in price_qty_pairs]


def _make_asks(price_qty_pairs):
    return [(str(p), str(q)) for p, q in price_qty_pairs]


def test_wall_detection_large_level():
    # avg bid qty = 1, threshold_multiplier=3 → need >3
    bids = _make_bids([(100, 1), (99, 1), (98, 1), (97, 1), (96, 200)])  # 200 >> 3×avg
    asks = _make_asks([(101, 1)] * 5)
    wd = WallDetector(min_notional_usd=0.0, threshold_multiplier=3.0, depth_levels=10)
    result = wd.update(bids, asks, price=100.0, now_ms=1000)
    assert len(result.bid_walls) > 0
    wall = result.bid_walls[0]
    assert wall.price == "96"
    assert float(wall.qty) == 200.0


def test_no_wall_below_notional():
    bids = _make_bids([(100, 200)])  # price×qty = 20000
    asks = _make_asks([(101, 1)])
    wd = WallDetector(min_notional_usd=50_000.0, threshold_multiplier=1.0, depth_levels=10)
    result = wd.update(bids, asks, price=100.0, now_ms=1000)
    assert len(result.bid_walls) == 0


def test_wall_persistence():
    bids = _make_bids([(100, 1), (99, 1), (98, 500)])
    asks = _make_asks([(101, 1)])
    wd = WallDetector(min_notional_usd=0.0, threshold_multiplier=2.0, depth_levels=10)
    wd.update(bids, asks, price=100.0, now_ms=1000)
    result = wd.update(bids, asks, price=100.0, now_ms=2000)
    wall = next(w for w in result.bid_walls if w.price == "98")
    assert wall.first_seen_ms == 1000
    assert wall.last_seen_ms == 2000


def test_spoofing_candidate_on_fast_disappearance():
    bids = _make_bids([(100, 1), (99, 1), (98, 500)])
    asks = _make_asks([(101, 1)])
    wd = WallDetector(
        min_notional_usd=0.0,
        threshold_multiplier=2.0,
        depth_levels=10,
        spoofing_max_age_s=60.0,
    )
    wd.update(bids, asks, price=100.0, now_ms=1000)
    # Wall disappears after 5 seconds (< 60s threshold)
    new_bids = _make_bids([(100, 1), (99, 1)])  # 98 gone
    result = wd.update(new_bids, asks, price=100.0, now_ms=6000)
    spoof_walls = [w for w in result.bid_walls if w.is_spoofing_candidate]
    assert len(spoof_walls) > 0
    assert result.spoofing_alert is True


def test_no_spoofing_for_old_wall():
    bids = _make_bids([(100, 1), (99, 1), (98, 500)])
    asks = _make_asks([(101, 1)])
    wd = WallDetector(
        min_notional_usd=0.0,
        threshold_multiplier=2.0,
        depth_levels=10,
        spoofing_max_age_s=5.0,
    )
    wd.update(bids, asks, price=100.0, now_ms=1000)
    new_bids = _make_bids([(100, 1), (99, 1)])
    result = wd.update(new_bids, asks, price=100.0, now_ms=10_000)  # 9s later, > 5s threshold
    spoof_walls = [w for w in result.bid_walls if w.is_spoofing_candidate]
    assert len(spoof_walls) == 0
    assert result.spoofing_alert is False
