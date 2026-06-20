import fnmatch
from dataclasses import dataclass, field


@dataclass
class SymbolPolicy:
    allowed_symbols: list[str] | None = None  # None means all allowed
    denied_symbols: list[str] = field(default_factory=list)

    def is_allowed(self, symbol: str) -> bool:
        if symbol in self.denied_symbols:
            return False
        if any(fnmatch.fnmatch(symbol, pat) for pat in self.denied_symbols):
            return False
        if self.allowed_symbols is None:
            return True
        return any(
            symbol == pat or fnmatch.fnmatch(symbol, pat)
            for pat in self.allowed_symbols
        )


def check_symbol_allowed(
    symbol: str,
    allowed: list[str] | None,
    denied: list[str],
) -> bool:
    return SymbolPolicy(allowed_symbols=allowed, denied_symbols=denied).is_allowed(symbol)
