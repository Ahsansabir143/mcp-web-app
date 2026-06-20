from __future__ import annotations

# Binance USDC-perpetual contracts carry a ".P" suffix (e.g. "BTCUSDT.P").
# All canonical symbols in this platform are uppercase with no suffix.

_STRIP_SUFFIXES = (".P",)


def normalize_symbol(raw: str) -> str:
    """Return uppercase canonical symbol, stripping any exchange-specific suffixes."""
    s = raw.upper().strip()
    for suffix in _STRIP_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def symbol_from_stream(source_stream: str) -> str | None:
    """Extract and normalize the symbol prefix from a combined-stream name.

    E.g. "btcusdt@trade" → "BTCUSDT", "user_data.ACCOUNT_UPDATE" → None.
    """
    at = source_stream.find("@")
    if at > 0:
        return normalize_symbol(source_stream[:at])
    return None
