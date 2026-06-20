from __future__ import annotations


def compute_funding_pressure(
    funding_rate: str,
    mark_price: str,
    index_price: str,
) -> float | None:
    """Return a funding pressure score in basis-points × 100 (i.e. 100 bps = 1%).

    Positive score → longs paying → bearish funding pressure.
    Negative score → shorts paying → bullish funding pressure.

    The base score is the funding rate expressed in basis points.
    A mark/index divergence term is added as an amplifier because divergence
    between mark and index price predicts the funding rate direction.
    """
    try:
        rate = float(funding_rate)
        mark = float(mark_price)
        index = float(index_price)
    except (ValueError, TypeError):
        return None

    base_score = rate * 10_000.0  # funding rate in bps

    divergence_bps = 0.0
    if index > 0:
        divergence_bps = ((mark - index) / index) * 10_000.0

    return base_score + divergence_bps * 0.5
