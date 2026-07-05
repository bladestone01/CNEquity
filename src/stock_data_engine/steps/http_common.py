"""Shared step helper for EastMoney / CNINFO HTTP datasets."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.steps.common import write_simple


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


def empty_ok(df: pl.DataFrame, dataset: str, trade_date: date) -> None:
    if df.is_empty():
        raise RuntimeError(f"{dataset}: no rows returned for {trade_date.isoformat()}")
