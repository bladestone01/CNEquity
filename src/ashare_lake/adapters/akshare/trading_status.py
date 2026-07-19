"""AKShare current ST snapshot — a robust supplement to EastMoney's ST list.

AKShare exposes only the *current* ST set (no dated history), so this augments
daily labeling and accumulates ST history going forward. Historical ST labels
before coverage are not available from free sources; historical *suspension* is
reconstructed separately from daily_bars gaps (derive/trading_status_history).
"""

from __future__ import annotations

import logging

from ashare_lake.domain.symbols import format_symbol, is_all_a_symbol

logger = logging.getLogger(__name__)


def _exchange_from_code(code: str) -> str:
    if code.startswith(("60", "68")):
        return "SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "BJ"
    return "SZ"


def fetch_st_symbols_akshare(*, config=None) -> set[str]:
    """Return the current ST / *ST symbol set (empty if akshare is unavailable)."""
    try:
        import akshare as ak  # type: ignore[import-not-found]
    except ImportError:
        return set()

    if config is not None:
        config.rate_limit("akshare")
    try:
        df = ak.stock_zh_a_st_em()
    except Exception as exc:
        logger.warning("akshare stock_zh_a_st_em failed: %s", exc)
        return set()
    if df is None or len(df) == 0 or "代码" not in df.columns:
        return set()

    symbols: set[str] = set()
    for code in df["代码"].astype(str):
        code = code.zfill(6)
        exch = _exchange_from_code(code)
        if is_all_a_symbol(code, exch):
            symbols.add(format_symbol(code, exch))
    return symbols
