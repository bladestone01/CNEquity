"""Python read API — load curated datasets with adjustment, universe, and PIT filters."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

from stock_data_engine.config import Config, load_config
from stock_data_engine.derive.adj_factors import STORED_ADJUST_TYPE
from stock_data_engine.domain.schemas import DATASET_SCHEMAS, validate_dataframe
from stock_data_engine.query.parquet_scan import (
    collect_parquet_root,
    dataset_has_parquet,
    partition_col_for_dataset,
)
from stock_data_engine.query.universe import apply_universe_filter

logger = logging.getLogger(__name__)

AdjustType = Literal["qfq", "hfq"]
UniverseType = Literal["all_a"]

CURATED_DATASETS = frozenset(
    {
        "instruments",
        "trading_calendar",
        "trading_status",
        "daily_bars",
        "index_bars",
        "corporate_actions",
        "financial_statement_items",
        "fund_flow",
        "margin_trading",
        "northbound_holdings",
        "northbound_flows",
        "valuation_metrics",
        "sector_members",
        "announcement_index",
        "dragon_tiger",
        "block_trades",
        "index_constituents",
        "industry_members",
        "macro_indicators",
        "market_breadth",
        "share_unlock_schedule",
        "regulatory_events",
        "institutional_holdings",
        "analyst_consensus",
        "sentiment_scores",
    }
)
DERIVED_DATASETS = frozenset({"adj_factors"})

DATE_COLUMNS: dict[str, str] = {
    "daily_bars": "trade_date",
    "index_bars": "trade_date",
    "trading_calendar": "trade_date",
    "trading_status": "trade_date",
    "corporate_actions": "ex_date",
    "adj_factors": "trade_date",
    "fund_flow": "trade_date",
    "margin_trading": "trade_date",
    "northbound_holdings": "trade_date",
    "northbound_flows": "trade_date",
    "valuation_metrics": "trade_date",
    "sector_members": "as_of_date",
    "announcement_index": "announce_date",
    "dragon_tiger": "trade_date",
    "block_trades": "trade_date",
    "index_constituents": "as_of_date",
    "industry_members": "as_of_date",
    "macro_indicators": "obs_date",
    "market_breadth": "trade_date",
    "share_unlock_schedule": "unlock_date",
    "regulatory_events": "event_date",
    "institutional_holdings": "report_period",
    "analyst_consensus": "forecast_date",
    "sentiment_scores": "trade_date",
}

PIT_DATASETS = frozenset({"financial_statement_items", "announcement_index"})
PRICE_COLS = ("open", "high", "low", "close")


class ReaderError(ValueError):
    """Raised when load() arguments or dataset state are invalid."""


def _parse_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def resolve_config(
    *,
    config: Config | None = None,
    data_root: str | Path | None = None,
) -> Config:
    if config is not None:
        return config
    if data_root is not None:
        return Config(data_root=Path(data_root).expanduser().resolve())
    path = Path("configs/stockdata.toml")
    if path.exists():
        return load_config(path)
    raise ReaderError(
        "No config found: pass config= or data_root=, or create configs/stockdata.toml"
    )


def _dataset_root(config: Config, dataset: str) -> Path:
    if dataset in DERIVED_DATASETS:
        return config.derived_root / dataset
    if dataset in CURATED_DATASETS:
        return config.curated_root / dataset
    raise ReaderError(f"unknown dataset {dataset!r}")


def _read_dataset(
    config: Config,
    dataset: str,
    *,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
) -> pl.DataFrame:
    root = _dataset_root(config, dataset)
    if not dataset_has_parquet(root):
        raise ReaderError(
            f"no parquet data for dataset {dataset!r} under {root} "
            f"(data_root={config.data_root})"
        )

    partition_col = DATE_COLUMNS.get(dataset) or partition_col_for_dataset(dataset)
    try:
        df = collect_parquet_root(
            root,
            partition_col=partition_col,
            start=start,
            end=end,
            symbols=symbols,
        )
    except FileNotFoundError as exc:
        raise ReaderError(
            f"no parquet data for dataset {dataset!r} under {root} "
            f"(data_root={config.data_root})"
        ) from exc
    if dataset in DATASET_SCHEMAS:
        return validate_dataframe(df, dataset)
    return df


def _apply_date_range(
    df: pl.DataFrame,
    dataset: str,
    start: date | None,
    end: date | None,
) -> pl.DataFrame:
    col = DATE_COLUMNS.get(dataset)
    if col is None or col not in df.columns or df.is_empty():
        return df
    if start is not None:
        df = df.filter(pl.col(col) >= start)
    if end is not None:
        df = df.filter(pl.col(col) <= end)
    return df


def _apply_symbol_filter(df: pl.DataFrame, symbols: list[str] | None) -> pl.DataFrame:
    if not symbols or df.is_empty() or "symbol" not in df.columns:
        return df
    return df.filter(pl.col("symbol").is_in(symbols))


def _hfq_anchor_factors(
    factors: pl.DataFrame,
    bars: pl.DataFrame,
    end: date | None,
) -> pl.DataFrame:
    """Per-symbol hfq factor at the qfq anchor date (latest date in scope)."""
    if end is not None:
        anchor_bars = bars.filter(pl.col("trade_date") <= end)
        anchor_factors = factors.filter(pl.col("trade_date") <= end)
    else:
        anchor_bars = bars
        anchor_factors = factors

    bar_anchors = anchor_bars.group_by("symbol").agg(pl.col("trade_date").max().alias("anchor_date"))
    return (
        anchor_factors.join(bar_anchors, on="symbol")
        .filter(pl.col("trade_date") == pl.col("anchor_date"))
        .select(["symbol", pl.col("factor").alias("hfq_anchor")])
    )


def _apply_adjustment(
    bars: pl.DataFrame,
    config: Config,
    adjust: AdjustType,
    start: date | None,
    end: date | None,
    *,
    strict_adj: bool = False,
) -> pl.DataFrame:
    if bars.is_empty():
        return bars

    factors = _read_dataset(config, "adj_factors", start=start, end=end)
    if factors.is_empty():
        out = bars.with_columns(
            *[
                pl.col(c).alias(f"adj_{c}")
                for c in PRICE_COLS
                if c in bars.columns
            ],
            pl.lit(False).alias("adj_is_exact"),
        )
        if strict_adj:
            raise ReaderError("adj_factors dataset is empty; cannot compute exact adjusted prices")
        logger.warning("adj_factors missing; adj_is_exact=False for all rows")
        return out

    factors = factors.filter(pl.col("adjust_type") == STORED_ADJUST_TYPE)
    if factors.is_empty():
        raise ReaderError(
            f"adj_factors has no {STORED_ADJUST_TYPE!r} rows; re-run derive (ADR-0004)"
        )

    factors = factors.select(["symbol", "trade_date", "factor"])

    if adjust == "qfq":
        anchors = _hfq_anchor_factors(factors, bars, end)
        factors = factors.join(anchors, on="symbol", how="left")
        factors = factors.with_columns(
            (pl.col("factor") / pl.col("hfq_anchor")).alias("factor")
        ).drop("hfq_anchor")
    elif adjust != "hfq":
        raise ReaderError(f"unsupported adjust type {adjust!r}")

    joined = bars.join(factors, on=["symbol", "trade_date"], how="left")
    joined = joined.with_columns(pl.col("factor").is_not_null().alias("adj_is_exact"))
    inexact = joined.filter(~pl.col("adj_is_exact")).height
    if inexact:
        msg = f"{inexact} bar row(s) missing adj_factors for adjust={adjust!r}"
        if strict_adj:
            raise ReaderError(msg)
        logger.warning("%s; using factor=1.0 with adj_is_exact=False", msg)
    joined = joined.with_columns(pl.col("factor").fill_null(1.0))
    adj_exprs = [
        (pl.col(c) * pl.col("factor")).alias(f"adj_{c}") for c in PRICE_COLS if c in joined.columns
    ]
    return joined.with_columns(adj_exprs).drop("factor")


def _apply_pit_filters(
    df: pl.DataFrame,
    *,
    as_of: date,
    items: list[str] | None,
) -> pl.DataFrame:
    if df.is_empty():
        return df
    if "announce_date" not in df.columns:
        raise ReaderError("financial_statement_items requires announce_date column (PIT contract)")
    df = df.filter(pl.col("announce_date") <= as_of)
    if items and "item_code" in df.columns:
        df = df.filter(pl.col("item_code").is_in(items))
    return df


def load(
    dataset: str,
    *,
    start: str | date | None = None,
    end: str | date | None = None,
    adjust: AdjustType | None = None,
    universe: UniverseType | None = None,
    as_of: str | date | None = None,
    items: list[str] | None = None,
    symbols: list[str] | None = None,
    strict_adj: bool = False,
    config: Config | None = None,
    data_root: str | Path | None = None,
) -> pl.DataFrame:
    """Load a curated dataset with optional adjustment, universe, and PIT filters.

    Parameters
    ----------
    dataset:
        Curated dataset name (e.g. ``daily_bars``, ``financial_statement_items``).
    start, end:
        Inclusive date window on the dataset's primary date column.
    adjust:
        ``qfq`` or ``hfq`` — joins stored ``hfq`` ``adj_factors`` and adds
        ``adj_open`` … ``adj_close`` plus ``adj_is_exact``. ``qfq`` is derived
        at query time as ``hfq_factor / hfq_anchor`` (anchor = latest bar date
        in scope); only ``hfq`` is persisted (ADR-0004).
    universe:
        ``all_a`` — drop unlisted/delisted rows per day via ``instruments``, and
        drop ST/suspended rows when ``trading_status`` has data for that day.
        Only valid for ``daily_bars`` (not ``index_bars``).

        **Limitation:** ``trading_status`` is daily-only (no historical ST
        backfill). Dates before the curated coverage start (see audit check
        ``trading_status_coverage_start``) are **not** ST/suspended-filtered;
        long backtests may include ST names in early windows.
    as_of:
        Point-in-time date for ``financial_statement_items`` (filters ``announce_date``).
    items:
        ``item_code`` filter for ``financial_statement_items``.
    symbols:
        Restrict to these symbols when the dataset has a ``symbol`` column.
    config, data_root:
        Lake location; auto-detects ``configs/stockdata.toml`` when omitted.
        Raises ``ReaderError`` if config or dataset parquet files are missing.
    """
    cfg = resolve_config(config=config, data_root=data_root)
    if dataset not in CURATED_DATASETS | DERIVED_DATASETS:
        raise ReaderError(f"unknown dataset {dataset!r}")

    if universe and dataset == "index_bars":
        raise ReaderError(
            "universe filter applies to daily_bars only; index symbols are not in all_a"
        )

    start_d = _parse_date(start)
    end_d = _parse_date(end)
    as_of_d = _parse_date(as_of)

    if dataset in PIT_DATASETS:
        if as_of_d is None:
            raise ReaderError(f"{dataset} requires as_of= for point-in-time queries")
        df = _read_dataset(cfg, dataset, symbols=symbols)
        df = _apply_pit_filters(df, as_of=as_of_d, items=items)
        df = _apply_symbol_filter(df, symbols)
        sort_cols = [c for c in ("announce_date", "symbol", "report_period", "item_code") if c in df.columns]
        return df.sort(sort_cols) if sort_cols else df

    df = _read_dataset(cfg, dataset, start=start_d, end=end_d, symbols=symbols)

    if universe and dataset == "daily_bars":
        date_col = DATE_COLUMNS[dataset]
        df = apply_universe_filter(df, cfg, universe=universe, date_col=date_col)

    if adjust and dataset in {"daily_bars", "index_bars"}:
        df = _apply_adjustment(df, cfg, adjust, start_d, end_d, strict_adj=strict_adj)

    sort_cols = [c for c in (DATE_COLUMNS.get(dataset), "symbol") if c and c in df.columns]
    if sort_cols:
        df = df.sort(sort_cols)
    return df
