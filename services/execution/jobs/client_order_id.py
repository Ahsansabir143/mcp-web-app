from __future__ import annotations

import hashlib


def make_client_order_id(
    intent_id: str,
    account_id: str,
    symbol: str,
    side: str,
) -> str:
    """Generate a deterministic, replay-safe client_order_id (36 chars).

    Format: "tp2-{32 hex chars}" — stable across replays of the same intent.
    Binance newClientOrderId limit is 36 characters.
    """
    raw = f"{intent_id}:{account_id}:{symbol}:{side}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"tp2-{digest[:32]}"
