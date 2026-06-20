import pytest
from services.analytics.engines.book_integrity import BookIntegrityState


def test_initial_state_invalid():
    s = BookIntegrityState()
    assert not s.has_snapshot
    assert not s.is_valid
    assert s.last_update_id == 0


def test_snapshot_sets_valid():
    s = BookIntegrityState()
    s.on_snapshot(last_update_id=100, received_ms=1000)
    assert s.has_snapshot
    assert s.is_valid
    assert s.last_update_id == 100
    assert s.gap_detected_at_ms is None
    assert s.deltas_applied == 0


def test_snapshot_resets_gap():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    s.on_delta(200, 300, 2000)  # gap
    assert not s.is_valid
    s.on_snapshot(300, 3000)
    assert s.is_valid
    assert s.gap_detected_at_ms is None
    assert s.invalidated_reason is None


def test_delta_before_snapshot_skipped():
    s = BookIntegrityState()
    result = s.on_delta(1, 5, 1000)
    assert result is False
    assert s.deltas_skipped == 1
    assert not s.has_snapshot


def test_in_sequence_delta_accepted():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    result = s.on_delta(101, 110, 2000)
    assert result is True
    assert s.last_update_id == 110
    assert s.deltas_applied == 1
    assert s.is_valid


def test_stale_delta_discarded():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    s.on_delta(101, 110, 2000)
    # Stale: last_update_id <= current position
    result = s.on_delta(85, 100, 3000)
    assert result is False
    assert s.last_update_id == 110  # unchanged
    assert s.deltas_skipped == 1


def test_gap_invalidates():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    result = s.on_delta(105, 115, 2000)   # first_update_id=105 > 100+1=101 → gap
    assert result is False
    assert not s.is_valid
    assert s.gap_detected_at_ms == 2000
    assert "sequence gap" in s.invalidated_reason


def test_multiple_sequential_deltas():
    s = BookIntegrityState()
    s.on_snapshot(50, 1000)
    assert s.on_delta(51, 60, 2000)
    assert s.on_delta(61, 70, 3000)
    assert s.on_delta(71, 80, 4000)
    assert s.last_update_id == 80
    assert s.deltas_applied == 3
    assert s.is_valid


def test_to_dict():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    d = s.to_dict()
    assert d["has_snapshot"] is True
    assert d["is_valid"] is True
    assert d["last_update_id"] == 100


def test_invalidate_method():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    s.invalidate("test reason", 2000)
    assert not s.is_valid
    assert s.invalidated_reason == "test reason"


def test_gap_size_reported():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    s.on_delta(200, 250, 2000)
    assert "gap=99" in s.invalidated_reason


def test_snapshot_resets_counters():
    s = BookIntegrityState()
    s.on_snapshot(100, 1000)
    s.on_delta(101, 110, 2000)
    s.on_delta(150, 160, 3000)  # gap
    s.on_snapshot(160, 4000)    # re-seed
    assert s.deltas_applied == 0
    assert s.deltas_skipped == 0
