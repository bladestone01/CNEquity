"""EastMoney corporate actions queried through migrated 920xxx BJ codes.

Legacy 43/83/87xxx names were moved to 920xxx codes in the BSE migration. The
old code is still the symbol in the lake (and in the historical bars), while
EastMoney's current report keeps the event history under the 920xxx identity.
This adapter is deliberately explicit and per-symbol; it must not change the
normal full-report daily/backfill path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path

import polars as pl

from cnequity.adapters.eastmoney.corporate_actions import (
    _COLUMNS,
    _parse_rows,
)
from cnequity.adapters.eastmoney.datacenter import fetch_datacenter
from cnequity.adapters.eastmoney.em_auth import EastMoneyClient
from cnequity.config import Config
from cnequity.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

__all__ = ["fetch_corporate_actions_eastmoney_migrated_bj"]


_OUTPUT_SCHEMA = {
    "symbol": pl.Utf8,
    "ex_date": pl.Date,
    "action_type": pl.Utf8,
    "cash_dividend": pl.Float64,
    "bonus_ratio": pl.Float64,
    "transfer_ratio": pl.Float64,
    "allotment_ratio": pl.Float64,
    "allotment_price": pl.Float64,
}

# Snapshot of BSE's published old/new code table (bse.cn/service/code_mapping.html).
# It is data, not a suffix heuristic: collisions such as 870726→920926 and
# 873305→920505 are real and would otherwise query another company's history.
_MAPPING_PATH = Path(__file__).with_name("seeds") / "bse_code_mapping.json"


@lru_cache(maxsize=1)
def _code_mapping() -> dict[str, str]:
    try:
        raw = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"BSE legacy/current code mapping is unreadable: {_MAPPING_PATH}"
        ) from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"BSE legacy/current code mapping is not an object: {_MAPPING_PATH}")
    return {str(old): str(new) for old, new in raw.items()}


def _migrated_code(symbol: str) -> str | None:
    """Map one legacy BJ code to the current 920xxx code, if applicable."""
    try:
        info = parse_symbol(symbol)
    except ValueError:
        return None
    if info.exchange != "BJ" or len(info.code) != 6 or not info.code.isdigit():
        return None
    return _code_mapping().get(info.code)


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_OUTPUT_SCHEMA)


def fetch_corporate_actions_eastmoney_migrated_bj(
    symbols: list[str],
    start: date,
    end: date,
    *,
    config: Config | None = None,
    symbol_windows: Mapping[str, tuple[date, date]] | None = None,
    client: EastMoneyClient | None = None,
) -> tuple[pl.DataFrame, list[str]]:
    """Fetch actions for legacy BJ symbols through current 920xxx identities."""
    owns_client = client is None
    if client is None:
        client = EastMoneyClient(config=config)

    rows: list[dict] = []
    failed: list[str] = []
    seen: set[str] = set()
    try:
        for symbol in symbols:
            if symbol in seen:
                continue
            seen.add(symbol)
            current_code = _migrated_code(symbol)
            if current_code is None:
                continue
            window = symbol_windows.get(symbol, (start, end)) if symbol_windows else (start, end)
            if window[0] > window[1]:
                continue
            try:
                raw = fetch_datacenter(
                    client,
                    "RPT_SHAREBONUS_DET",
                    _COLUMNS,
                    filter_expr=f'(SECURITY_CODE="{current_code}")',
                    page_size=500,
                    sort_columns="EX_DIVIDEND_DATE",
                    sort_types="1",
                    max_retries=config.max_retries if config is not None else 3,
                    retry_backoff_seconds=(
                        float(config.retry_backoff_seconds) if config is not None else 5.0
                    ),
                )
                for item in raw:
                    for parsed in _parse_rows(item):
                        if not (window[0] <= parsed["ex_date"] <= window[1]):
                            continue
                        parsed["symbol"] = symbol
                        rows.append(parsed)
            except Exception as exc:  # noqa: BLE001 — preserve other symbols for retry
                failed.append(symbol)
                logger.warning(
                    "eastmoney migrated corporate_actions: failed for %s/%s: %s",
                    symbol,
                    current_code,
                    exc,
                )
    finally:
        if owns_client:
            client.close()

    if not rows:
        return _empty(), failed
    frame = pl.DataFrame(rows, schema_overrides={"allotment_price": pl.Float64})
    return (
        frame.unique(subset=["symbol", "ex_date", "action_type"], keep="last").sort(
            ["ex_date", "symbol", "action_type"]
        ),
        failed,
    )
