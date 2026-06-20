"""Lightweight in-process metrics registry.

Each service process gets its own singleton MetricsRegistry.
Use get_registry() to obtain it.

Usage:
    from shared.metrics import get_registry
    metrics = get_registry()
    metrics.increment("jobs_processed")
    metrics.increment("jobs_failed")
    metrics.set_gauge("active_sessions", len(registry))
    snapshot = metrics.snapshot()  # → {"counters": {...}, "gauges": {...}}
"""
from __future__ import annotations

import threading
from collections import defaultdict


class MetricsRegistry:
    """Thread-safe counter and gauge store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def reset_counter(self, name: str) -> None:
        with self._lock:
            self._counters[name] = 0

    def get_counter(self, name: str) -> int:
        with self._lock:
            return self._counters[name]

    def get_gauge(self, name: str) -> float | None:
        with self._lock:
            return self._gauges.get(name)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }


_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    """Return the module-level singleton registry for this process."""
    return _registry
