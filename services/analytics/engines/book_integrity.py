from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BookIntegrityState:
    """Tracks orderbook sequence continuity for a single symbol.

    Binance depth stream rule:
        When applying update U..u: the first update's U must be <= lastUpdateId+1
        and u must be >= lastUpdateId+1.  If U > lastUpdateId+1, a gap exists and
        the book must be re-seeded from a fresh REST snapshot.
    """

    has_snapshot: bool = False
    last_update_id: int = 0
    is_valid: bool = False
    gap_detected_at_ms: int | None = None
    invalidated_reason: str | None = None
    snapshot_received_at_ms: int = 0
    deltas_applied: int = 0
    deltas_skipped: int = 0

    def on_snapshot(self, last_update_id: int, received_ms: int) -> None:
        self.has_snapshot = True
        self.last_update_id = last_update_id
        self.is_valid = True
        self.gap_detected_at_ms = None
        self.invalidated_reason = None
        self.snapshot_received_at_ms = received_ms
        self.deltas_applied = 0
        self.deltas_skipped = 0

    def on_delta(self, first_update_id: int, last_update_id: int, received_ms: int) -> bool:
        """Apply a depth update.  Returns True if the delta is valid and in-sequence."""
        if not self.has_snapshot:
            self.deltas_skipped += 1
            return False

        # Stale: this delta is entirely behind our position — silently discard
        if last_update_id <= self.last_update_id:
            self.deltas_skipped += 1
            return False

        # Gap: the first update id jumps past where we are — book is now invalid
        expected_next = self.last_update_id + 1
        if first_update_id > expected_next:
            self.is_valid = False
            self.gap_detected_at_ms = received_ms
            self.invalidated_reason = (
                f"sequence gap: expected first_update_id<={expected_next}, "
                f"got {first_update_id} (gap={first_update_id - expected_next})"
            )
            self.deltas_skipped += 1
            return False

        self.last_update_id = last_update_id
        self.deltas_applied += 1
        return True

    def invalidate(self, reason: str, received_ms: int) -> None:
        self.is_valid = False
        self.gap_detected_at_ms = received_ms
        self.invalidated_reason = reason

    def to_dict(self) -> dict:
        return {
            "has_snapshot": self.has_snapshot,
            "is_valid": self.is_valid,
            "last_update_id": self.last_update_id,
            "gap_detected_at_ms": self.gap_detected_at_ms,
            "invalidated_reason": self.invalidated_reason,
            "deltas_applied": self.deltas_applied,
            "deltas_skipped": self.deltas_skipped,
        }
