from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpreadResult:
    spread: float
    spread_bps: float
    mid_price: float
    bid: float
    ask: float
    bid_size: float
    ask_size: float


@dataclass
class ImbalanceResult:
    imbalance_ratio: float   # −1 (ask heavy) → +1 (bid heavy)
    bid_depth_usd: float
    ask_depth_usd: float


@dataclass
class WallInfo:
    price: str
    qty: str
    side: str                     # "bid" | "ask"
    first_seen_ms: int
    last_seen_ms: int
    is_spoofing_candidate: bool = False
    disappear_count: int = 0


@dataclass
class WallsResult:
    bid_walls: list[WallInfo] = field(default_factory=list)
    ask_walls: list[WallInfo] = field(default_factory=list)
    spoofing_alert: bool = False


def compute_spread(
    bid: str, ask: str, bid_size: str, ask_size: str
) -> SpreadResult | None:
    try:
        b = float(bid)
        a = float(ask)
        bs = float(bid_size)
        as_ = float(ask_size)
    except (ValueError, TypeError):
        return None
    if b <= 0 or a <= 0 or a <= b:
        return None
    mid = (b + a) / 2.0
    spread = a - b
    spread_bps = (spread / mid) * 10_000.0
    return SpreadResult(
        spread=spread, spread_bps=spread_bps, mid_price=mid,
        bid=b, ask=a, bid_size=bs, ask_size=as_,
    )


def compute_imbalance(
    bids: list,
    asks: list,
    price: float,
    top_n: int = 10,
) -> ImbalanceResult | None:
    try:
        b_levels = bids[:top_n]
        a_levels = asks[:top_n]
        bid_qty = sum(float(q) for _, q in b_levels)
        ask_qty = sum(float(q) for _, q in a_levels)
        total = bid_qty + ask_qty
        if total == 0:
            return None
        ratio = (bid_qty - ask_qty) / total
        return ImbalanceResult(
            imbalance_ratio=ratio,
            bid_depth_usd=bid_qty * price,
            ask_depth_usd=ask_qty * price,
        )
    except (ValueError, TypeError, IndexError):
        return None


class WallDetector:
    """Detects resting walls and tracks spoofing candidates.

    A wall is a level whose notional exceeds min_notional_usd AND whose qty is
    at least threshold_multiplier × the average qty of the sampled levels.

    Spoofing heuristic: a wall that disappears within spoofing_max_age_s seconds
    without its price level being traded through is flagged as a spoofing candidate.
    """

    def __init__(
        self,
        min_notional_usd: float = 100_000.0,
        threshold_multiplier: float = 3.0,
        depth_levels: int = 20,
        spoofing_max_age_s: float = 30.0,
    ) -> None:
        self._min_notional = min_notional_usd
        self._threshold = threshold_multiplier
        self._depth = depth_levels
        self._spoof_age_s = spoofing_max_age_s
        self._known: dict[tuple[str, str], WallInfo] = {}  # (side, price) → WallInfo

    def update(
        self,
        bids: list,
        asks: list,
        price: float,
        now_ms: int,
    ) -> WallsResult:
        active: set[tuple[str, str]] = set()
        bid_walls = self._scan_side(bids[: self._depth], "bid", price, now_ms, active)
        ask_walls = self._scan_side(asks[: self._depth], "ask", price, now_ms, active)

        # Process disappearances
        for key in list(self._known.keys()):
            if key not in active:
                wall = self._known.pop(key)
                age_s = (now_ms - wall.first_seen_ms) / 1000.0
                if age_s < self._spoof_age_s:
                    wall.is_spoofing_candidate = True
                    wall.disappear_count += 1
                    # Keep in result lists even though disappeared (for spoofing alert)
                    if wall.side == "bid":
                        bid_walls.append(wall)
                    else:
                        ask_walls.append(wall)

        spoofing_alert = any(w.is_spoofing_candidate for w in bid_walls + ask_walls)
        return WallsResult(bid_walls=bid_walls, ask_walls=ask_walls, spoofing_alert=spoofing_alert)

    def _scan_side(
        self,
        levels: list,
        side: str,
        price: float,
        now_ms: int,
        active: set,
    ) -> list[WallInfo]:
        if not levels:
            return []
        qtys = []
        for item in levels:
            try:
                q = float(item[1])
                if q > 0:
                    qtys.append(q)
            except (ValueError, IndexError):
                pass
        if not qtys:
            return []
        avg_qty = sum(qtys) / len(qtys)
        walls: list[WallInfo] = []
        for item in levels:
            try:
                p_str, q_str = str(item[0]), str(item[1])
                p = float(p_str)
                q = float(q_str)
            except (ValueError, IndexError):
                continue
            if q <= 0:
                continue
            if q * price < self._min_notional:
                continue
            if avg_qty == 0 or q < avg_qty * self._threshold:
                continue

            key = (side, p_str)
            active.add(key)
            if key in self._known:
                w = self._known[key]
                w.last_seen_ms = now_ms
                w.qty = q_str
            else:
                w = WallInfo(price=p_str, qty=q_str, side=side,
                             first_seen_ms=now_ms, last_seen_ms=now_ms)
                self._known[key] = w
            walls.append(w)
        return walls
