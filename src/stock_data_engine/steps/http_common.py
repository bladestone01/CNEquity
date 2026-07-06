"""Shared step helper for EastMoney / CNINFO HTTP datasets."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.steps.common import fetch_incremental_daily, write_simple


def write_fetched(
    config: Config,
    run_id: str,
    dataset: str,
    df: pl.DataFrame,
    *,
    source: str,
) -> dict:
    df = with_provenance(df, source=source, data_version="v1")
    return write_simple(config, run_id, dataset, df)


def run_incremental_fetched(
    config: Config,
    trade_date: date,
    run_id: str,
    dataset: str,
    fetch_fn: Callable[[date], pl.DataFrame],
    *,
    source: str,
    allow_empty: bool = False,
) -> dict:
    df = fetch_incremental_daily(
        config,
        dataset,
        trade_date,
        fetch_fn,
        allow_empty=allow_empty,
    )
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, dataset, df, source=source)


def empty_ok(df: pl.DataFrame, dataset: str, trade_date: date) -> None:
    if df.is_empty():
        raise RuntimeError(f"{dataset}: no rows returned for {trade_date.isoformat()}")
