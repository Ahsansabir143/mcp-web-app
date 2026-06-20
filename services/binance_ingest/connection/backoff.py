from __future__ import annotations

import random


class ExponentialBackoff:
    """Exponential backoff with full jitter.

    Delay for attempt n = random(0, min(max_s, base_s * factor^n)).
    Full jitter prevents thundering-herd on simultaneous reconnects.
    """

    def __init__(
        self,
        base_s: float = 1.0,
        max_s: float = 60.0,
        factor: float = 2.0,
    ) -> None:
        self._base = base_s
        self._max = max_s
        self._factor = factor
        self._attempt = 0

    def next_delay(self) -> float:
        cap = min(self._max, self._base * (self._factor ** self._attempt))
        self._attempt += 1
        return random.uniform(0.0, cap)

    def reset(self) -> None:
        self._attempt = 0
