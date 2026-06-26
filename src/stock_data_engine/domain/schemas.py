from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

PROVENANCE = ["source", "data_version", "fetched_at"]

DAILY_BARS_SCHEMA = {
    "symbol": pl.Utf8,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Utf8,
}

INSTRUMENTS_SCHEMA = {
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "exchange": pl.Utf8,
    "asset_type": pl.Utf8,
    "list_date": pl.Date,
    "delist_date": pl.Date,
    "prev_symbol": pl.Utf8,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Utf8,
}

TRADING_CALENDAR_SCHEMA = {
    "trade_date": pl.Date,
    "is_trading": pl.Boolean,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Utf8,
}

TRADING_STATUS_SCHEMA = {
    "symbol": pl.Utf8,
    "trade_date": pl.Date,
    "is_trading": pl.Boolean,
    "status": pl.Utf8,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Utf8,
}

CORPORATE_ACTIONS_SCHEMA = {
    "symbol": pl.Utf8,
    "ex_date": pl.Date,
    "action_type": pl.Utf8,
    "cash_dividend": pl.Float64,
    "bonus_ratio": pl.Float64,
    "transfer_ratio": pl.Float64,
    "allotment_ratio": pl.Float64,
    "allotment_price": pl.Float64,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Utf8,
}

ADJ_FACTORS_SCHEMA = {
    "symbol": pl.Utf8,
    "trade_date": pl.Date,
    "adjust_type": pl.Utf8,
    "factor": pl.Float64,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Utf8,
}

DATASET_SCHEMAS = {
    "instruments": INSTRUMENTS_SCHEMA,
    "trading_calendar": TRADING_CALENDAR_SCHEMA,
    "trading_status": TRADING_STATUS_SCHEMA,
    "daily_bars": DAILY_BARS_SCHEMA,
    "index_bars": {**DAILY_BARS_SCHEMA, "frequency": pl.Utf8},
    "corporate_actions": CORPORATE_ACTIONS_SCHEMA,
    "adj_factors": ADJ_FACTORS_SCHEMA,
}

PRIMARY_KEYS = {
    "instruments": ["symbol"],
    "trading_calendar": ["trade_date"],
    "trading_status": ["symbol", "trade_date"],
    "daily_bars": ["symbol", "trade_date"],
    "index_bars": ["symbol", "trade_date", "frequency"],
    "corporate_actions": ["symbol", "ex_date", "action_type"],
    "adj_factors": ["symbol", "trade_date", "adjust_type"],
}


class SchemaValidationError(ValueError):
    """Raised when a DataFrame does not match the dataset contract."""


def validate_dataframe(df: pl.DataFrame, dataset: str) -> pl.DataFrame:
    """Cast and validate *df* against the curated schema for *dataset*."""
    schema = DATASET_SCHEMAS.get(dataset)
    if schema is None:
        return df

    if df.is_empty():
        return pl.DataFrame(schema=schema)

    missing = [col for col in schema if col not in df.columns]
    if missing:
        raise SchemaValidationError(f"dataset '{dataset}': missing columns {missing}")

    casts = [pl.col(col).cast(dtype, strict=False) for col, dtype in schema.items()]
    return df.with_columns(casts).select(list(schema.keys()))


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def with_provenance(df: pl.DataFrame, source: str, data_version: str) -> pl.DataFrame:
    fetched = utc_now_iso()
    return df.with_columns(
        pl.lit(source).alias("source"),
        pl.lit(data_version).alias("data_version"),
        pl.lit(fetched).alias("fetched_at"),
    )
