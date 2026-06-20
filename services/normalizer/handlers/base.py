from __future__ import annotations

from dataclasses import dataclass, field

from shared.schemas.events import NormalizedEvent


@dataclass
class HotStateWrite:
    key: str
    value: str    # JSON-serialized value
    ttl_s: int | None = None


@dataclass
class HandlerResult:
    event: NormalizedEvent
    hot_writes: list[HotStateWrite] = field(default_factory=list)


class NormalizeError(Exception):
    """Raised when a raw payload cannot be normalized."""
