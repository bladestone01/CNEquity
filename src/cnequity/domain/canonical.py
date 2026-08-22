"""Canonical row helpers shared by storage, query, and quality consumers."""

from __future__ import annotations

import polars as pl

from cnequity.domain.datasets import DATASETS
from cnequity.domain.schemas import PRIMARY_KEYS

_SOURCE_RANK = "__canonical_source_rank"


def _source_rank_expr(dataset: str, columns: set[str]) -> pl.Expr | None:
    """Rank primary/backup sources for deterministic same-timestamp ties."""
    if "source" not in columns:
        return None
    spec = DATASETS.get(dataset)
    if spec is None:
        return pl.lit(0)
    rank = pl.lit(0)
    if spec.backup_source:
        rank = pl.when(pl.col("source") == spec.backup_source).then(1).otherwise(rank)
    if spec.primary_source:
        rank = pl.when(pl.col("source") == spec.primary_source).then(2).otherwise(rank)
    return rank


def _sort_for_canonical(frame, dataset: str):
    """Order freshest rows, then source priority, before PK collapse."""
    columns = set(frame.collect_schema().names()) if isinstance(frame, pl.LazyFrame) else set(frame.columns)
    sort_cols = ["fetched_at"]
    descending = [False]
    rank = _source_rank_expr(dataset, columns)
    if rank is not None:
        frame = frame.with_columns(rank.alias(_SOURCE_RANK))
        sort_cols.extend([_SOURCE_RANK, "source"])
        # ``unique(..., keep="last")`` selects the last row after sorting.
        # Primary therefore needs the greatest rank at the end of the sort.
        descending.extend([False, False])
    if "data_version" in columns:
        sort_cols.append("data_version")
        descending.append(False)
    return frame.sort(sort_cols, descending=descending, nulls_last=True, maintain_order=True)


def dedupe_by_primary_key(df: pl.DataFrame, dataset: str) -> pl.DataFrame:
    """Keep one row per registered PK, preferring the freshest provenance.

    Validated lake rows always carry ``fetched_at``. The no-provenance fallback
    still collapses duplicate keys so a malformed legacy fragment cannot
    multiply a quality join; schema checks remain responsible for reporting
    that the fragment is incomplete.
    """
    primary_key = PRIMARY_KEYS.get(dataset, [])
    if df.is_empty() or not primary_key or any(k not in df.columns for k in primary_key):
        return df
    if "fetched_at" in df.columns:
        df = _sort_for_canonical(df, dataset)
    out = df.unique(subset=primary_key, keep="last", maintain_order=True)
    return out.drop(_SOURCE_RANK, strict=False)


def dedupe_lazy_by_primary_key(lf: pl.LazyFrame, dataset: str) -> pl.LazyFrame:
    """Lazy equivalent of :func:`dedupe_by_primary_key`."""
    primary_key = PRIMARY_KEYS.get(dataset, [])
    columns = set(lf.collect_schema().names())
    if not primary_key or any(k not in columns for k in primary_key):
        return lf
    if "fetched_at" in columns:
        lf = _sort_for_canonical(lf, dataset)
    return lf.unique(subset=primary_key, keep="last", maintain_order=True).drop(
        _SOURCE_RANK, strict=False
    )
