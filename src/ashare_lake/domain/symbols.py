from __future__ import annotations

from dataclasses import dataclass

PREFIX_WHITELIST = {
    "SH": ("60", "68"),
    "SZ": ("00", "30"),
    "BJ": ("92",),
}

EXCLUDED_PREFIXES = tuple(f"{p}" for p in range(81, 90))

# SSE reserves 689xxx for CDRs (存托凭证). They trade on SH and stay in fetch
# scope (is_all_a_symbol), but are not common stock: primary sources (sina adj
# factors, tdx xdxr) have no coverage and the all_a selection universe excludes
# them (see query/universe.py).
CDR_PREFIXES = ("689",)

# Exchange-traded funds / LOFs. Kept in instruments + daily_bars for UI/quotes,
# but NOT in PREFIX_WHITELIST — all_a research universe excludes them.
ETF_PREFIXES = {
    "SH": ("51", "52", "56", "58"),
    "SZ": ("15", "16"),
}


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    code: str
    exchange: str


def parse_symbol(symbol: str) -> SymbolInfo:
    if "." not in symbol:
        raise ValueError(f"Invalid symbol format: {symbol}")
    code, exchange = symbol.rsplit(".", 1)
    exchange = exchange.upper()
    if exchange not in ("SH", "SZ", "BJ"):
        raise ValueError(f"Unknown exchange: {exchange}")
    return SymbolInfo(symbol=symbol, code=code, exchange=exchange)


def format_symbol(code: str, exchange: str) -> str:
    return f"{code}.{exchange.upper()}"


def is_all_a_symbol(code: str, exchange: str) -> bool:
    if any(code.startswith(p) for p in EXCLUDED_PREFIXES):
        return False
    prefixes = PREFIX_WHITELIST.get(exchange.upper(), ())
    return any(code.startswith(p) for p in prefixes)


def is_cdr_symbol(code: str, exchange: str) -> bool:
    """Whether *code* is a CDR (Chinese Depositary Receipt, SH 689xxx segment)."""
    return exchange.upper() == "SH" and any(code.startswith(p) for p in CDR_PREFIXES)


def is_etf_symbol(code: str, exchange: str) -> bool:
    """Whether *code* is an exchange-traded fund / LOF on SH/SZ."""
    prefixes = ETF_PREFIXES.get(exchange.upper(), ())
    return any(code.startswith(p) for p in prefixes)


def normalize_market_code(code: str, market: str) -> tuple[str, str]:
    market = market.lower()
    if market in ("sh", "1"):
        exchange = "SH"
    elif market in ("sz", "0"):
        exchange = "SZ"
    elif market in ("bj", "2"):
        exchange = "BJ"
    else:
        exchange = market.upper()
    return code.zfill(6), exchange
