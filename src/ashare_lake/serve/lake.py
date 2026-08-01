"""Read-only projection of one lake, shaped for the dashboard.

Every value here comes from something already on disk — the registry, the
directory layout, ``meta/stats``, ``meta/quality/health-latest.json``, the
manifest. **Nothing in this module scans curated.** A request that reads parquet
is a request that gets slower as the lake grows, which is the failure mode the
stats tables exist to prevent.

Two things this deliberately does *not* do:

* It does not open ``data/duckdb/ashare-lake.duckdb``. DuckDB allows many
  readers or one writer, so a held read handle would make
  ``ensure_duckdb_views()`` fail during the nightly run — the dashboard would
  break ingestion. Views are rebuilt in a private in-memory database instead;
  they are generated from the registry and cost milliseconds.
* It does not recompute audit findings. ``lake_health()`` walks the lake; the
  dashboard reads the JSON that ``asl audit --full`` already wrote. A page view
  must not cost what an audit costs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl

from ashare_lake.config import Config
from ashare_lake.domain.datasets import DATASETS, TIER_LABELS, TIERS, is_stale
from ashare_lake.storage.stats import (
    load_partition_stats,
    load_provenance_stats,
    refresh_stats_if_stale,
    stats_freshness,
)

logger = logging.getLogger(__name__)

# The catalog walks partition directories. Cheap, but the overview page fans out
# to several endpoints at once and they would each redo it.
_CACHE_TTL_SECONDS = 30.0

# Heatmap cell alphabet. One char per (dataset, day) keeps a 40x250 grid a few
# kilobytes instead of ten thousand JSON objects.
CELL_COVERED = "#"
CELL_GAP = "."
CELL_OUTSIDE = " "
CELL_UNPARTITIONED = "-"


@dataclass
class _Cached:
    value: Any
    at: float


def _next_period_start(day: date, granularity: str) -> date:
    """First day of the period after the one holding *day*."""
    if granularity == "year":
        return date(day.year + 1, 1, 1)
    if granularity == "quarter":
        quarter_end_month = 3 * ((day.month - 1) // 3) + 3
        return (
            date(day.year + 1, 1, 1)
            if quarter_end_month == 12
            else date(day.year, quarter_end_month + 1, 1)
        )
    if granularity == "month":
        return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
    return day + timedelta(days=1)


class LakeView:
    """Answers the dashboard's questions about one lake. Thread-safe."""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._cache: dict[str, _Cached] = {}
        self._refresh_lock = threading.Lock()
        self._refreshing = False

    # --- caching -----------------------------------------------------------

    def _cached(self, key: str, build):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and now - hit.at < _CACHE_TTL_SECONDS:
                return hit.value
        # Built outside the lock: two concurrent misses do the work twice, which
        # is cheaper than serialising every request behind one directory walk.
        value = build()
        with self._lock:
            self._cache[key] = _Cached(value, time.monotonic())
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    # --- background stats refresh -----------------------------------------

    def refresh_stats_in_background(self) -> bool:
        """Kick off a rebuild if ingestion has moved the lake. Never blocks.

        Threading lives here rather than in ``storage.stats`` so the module
        stays synchronous and testable. One thread at a time: the stats lock
        would already collapse duplicates, but spawning a thread per request to
        immediately lose a lock is waste.
        """
        with self._refresh_lock:
            if self._refreshing:
                return False
            if not stats_freshness(self.config).stale:
                return False
            self._refreshing = True

        def _run() -> None:
            try:
                result = refresh_stats_if_stale(self.config)
                if result is not None:
                    logger.info(
                        "stats rebuilt in background: %d dataset(s), %d row(s)",
                        len(result.datasets),
                        result.rows,
                    )
                    self.invalidate()
            except Exception:
                logger.exception("background stats refresh failed")
            finally:
                with self._refresh_lock:
                    self._refreshing = False

        threading.Thread(target=_run, name="stats-refresh", daemon=True).start()
        return True

    # --- primitives --------------------------------------------------------

    def anchor(self) -> date:
        """Last trading day — the date freshness is judged against."""

        def _build() -> date:
            from ashare_lake.steps.common import is_trading_day

            day = date.today()
            for _ in range(15):
                if is_trading_day(self.config, day):
                    return day
                day -= timedelta(days=1)
            return date.today()

        return self._cached("anchor", _build)

    def _catalog(self) -> pl.DataFrame:
        """``list_datasets()`` joined with the measured rows and bytes."""

        def _build() -> pl.DataFrame:
            from ashare_lake.query.reader import list_datasets

            catalog = list_datasets(config=self.config)
            stats = load_partition_stats(self.config)
            if stats.is_empty():
                return catalog.with_columns(
                    pl.lit(None, dtype=pl.Int64).alias("row_count"),
                    pl.lit(None, dtype=pl.Int64).alias("bytes"),
                    pl.lit(None, dtype=pl.Int64).alias("partitions"),
                )
            rollup = stats.group_by("dataset").agg(
                pl.col("row_count").sum(),
                pl.col("bytes").sum(),
                pl.len().alias("partitions"),
            )
            return catalog.join(rollup, on="dataset", how="left")

        return self._cached("catalog", _build)

    def _health_findings(self) -> dict:
        """The audit's last written health snapshot, or an empty stand-in."""

        def _build() -> dict:
            path = self.config.meta_root / "quality" / "health-latest.json"
            if not path.exists():
                return {}
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError):
                return {}

        return self._cached("health_findings", _build)

    def _freshness_of(self, row: dict, anchor: date) -> str:
        """fresh / STALE / empty / n/a, on the same rules as ``asl status``."""
        if not row["has_data"]:
            return "empty"
        if not row["watermarked"]:
            return "n/a"
        mark = row["watermark"] or row["coverage_end"]
        return "stale" if is_stale(row["dataset"], mark, anchor) else "fresh"

    def _rows(self) -> list[dict]:
        """One enriched dict per registered dataset."""
        anchor = self.anchor()
        out = []
        for row in self._catalog().iter_rows(named=True):
            spec = DATASETS[row["dataset"]]
            out.append(
                {
                    **row,
                    "tier": spec.tier,
                    "tier_label": TIER_LABELS[spec.tier],
                    "required": spec.required,
                    "intraday": spec.intraday_frequency,
                    "granularity": spec.partition_granularity if spec.partition_col else None,
                    "freshness": self._freshness_of(row, anchor),
                }
            )
        return out

    # --- endpoint payloads -------------------------------------------------

    def health(self) -> dict:
        rows = self._rows()
        findings = self._health_findings()
        freshness = stats_freshness(self.config)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["freshness"]] = counts.get(row["freshness"], 0) + 1
        return {
            "anchor": self.anchor(),
            "datasets": len(rows),
            "fresh": counts.get("fresh", 0),
            "stale": counts.get("stale", 0),
            "empty": counts.get("empty", 0),
            "not_applicable": counts.get("n/a", 0),
            "stale_datasets": sorted(r["dataset"] for r in rows if r["freshness"] == "stale"),
            # Empty is not automatically a problem: an opt-in dataset nobody
            # enabled and a required one that failed look identical on disk.
            "empty_optional": sorted(
                r["dataset"] for r in rows if r["freshness"] == "empty" and not r["required"]
            ),
            "empty_required": sorted(
                r["dataset"] for r in rows if r["freshness"] == "empty" and r["required"]
            ),
            "rows": sum(r["row_count"] or 0 for r in rows),
            "bytes": sum(r["bytes"] or 0 for r in rows),
            "findings_by_severity": findings.get("findings_by_severity", {}),
            "audit_trade_date": findings.get("trade_date"),
            "stats_stale": freshness.stale,
            "stats_reason": freshness.reason,
            "stats_generated_at": freshness.generated_at,
        }

    def tiers(self) -> list[dict]:
        rows = self._rows()
        out = []
        for tier in TIERS:
            members = [r for r in rows if r["tier"] == tier]
            if not members:
                continue
            out.append(
                {
                    "tier": tier,
                    "label": TIER_LABELS[tier],
                    "datasets": len(members),
                    "fresh": sum(1 for r in members if r["freshness"] == "fresh"),
                    "stale": sum(1 for r in members if r["freshness"] == "stale"),
                    "empty": sum(1 for r in members if r["freshness"] == "empty"),
                    "rows": sum(r["row_count"] or 0 for r in members),
                    "bytes": sum(r["bytes"] or 0 for r in members),
                    "members": [r["dataset"] for r in members],
                }
            )
        return out

    def datasets(self, *, tier: str | None = None) -> list[dict]:
        rows = self._rows()
        if tier:
            rows = [r for r in rows if r["tier"] == tier]
        return rows

    def provenance(self, dataset: str) -> list[dict]:
        """Source mix for one dataset, newest ``fetched_at`` first."""
        stats = load_provenance_stats(self.config)
        if stats.is_empty():
            return []
        rolled = (
            stats.filter(pl.col("dataset") == dataset)
            .group_by(["source", "data_version"])
            .agg(
                pl.col("row_count").sum(),
                pl.col("fetched_at_min").min(),
                pl.col("fetched_at_max").max(),
            )
            .sort("row_count", descending=True)
        )
        return rolled.to_dicts()

    # --- one dataset -------------------------------------------------------

    def partitions(self, dataset: str) -> list[dict]:
        """Per-partition rows and bytes, oldest first — the size/volume series."""
        stats = load_partition_stats(self.config)
        if stats.is_empty():
            return []
        rows = stats.filter(pl.col("dataset") == dataset)
        if rows.is_empty():
            return []
        return (
            rows.sort("period_start", nulls_last=True)
            .select("partition", "granularity", "period_start", "period_end", "row_count", "bytes")
            .to_dicts()
        )

    def _gaps(self, spec, parts: list[dict]) -> dict:
        """Periods inside the covered span that hold no partition.

        Counted in the dataset's own period, not in days: a year-partitioned
        dataset is not missing 364 days because one directory covers the year,
        and reporting it that way would drown the real gaps.
        """
        dated = [p for p in parts if p["period_start"] is not None]
        if len(dated) < 2:
            return {"missing": [], "total": 0, "unit": spec.partition_granularity}

        present = {p["partition"] for p in dated}
        first, last = dated[0]["period_start"], max(p["period_end"] for p in dated)
        missing: list[str] = []

        if spec.partition_granularity == "day":
            # Only sessions count as missing; a weekend is not a gap.
            from ashare_lake.steps.common import _load_trading_calendar_df

            calendar = _load_trading_calendar_df(self.config, start=first, end=last)
            if calendar is None or calendar.is_empty():
                return {"missing": [], "total": 0, "unit": "day"}
            for day in calendar.filter(pl.col("is_trading")).sort("trade_date")["trade_date"]:
                if day.isoformat() not in present:
                    missing.append(day.isoformat())
        else:
            from ashare_lake.domain.partitions import partition_value

            cursor = first
            while cursor <= last:
                value = partition_value(cursor, spec.partition_granularity)
                if value not in present:
                    missing.append(value)
                cursor = _next_period_start(cursor, spec.partition_granularity)

        return {
            "missing": missing[:60],
            "total": len(missing),
            "unit": spec.partition_granularity,
        }

    def _commands(self, spec, freshness: str) -> list[dict]:
        """What to run, and why. The dashboard names the fix; it does not run it."""
        name = spec.name
        out: list[dict] = []
        if spec.layer == "derived":
            out.append({"cmd": f"asl derive {name}", "why": "由 curated 重算"})
        elif spec.backfill_source:
            out.append(
                {"cmd": f"asl backfill {name}", "why": f"专用历史源：{spec.backfill_source}"}
            )
        elif spec.fetch_semantics == "by_date":
            out.append({"cmd": f"asl backfill {name}", "why": "按日期回补缺口"})
        if freshness == "stale":
            out.append({"cmd": "asl status", "why": "查看最近 run，再 asl retry --run-id"})
        out.append({"cmd": f"asl stats show --dataset {name}", "why": "逐分区行数与体积"})
        return out

    def recent_batches(self, dataset: str, *, limit: int = 15) -> list[dict]:
        """Latest manifest batches for this dataset, newest first.

        stdlib sqlite3 on a read-only URI rather than DuckDB's sqlite_scanner:
        that scanner is an autoloadable extension fetched from the network on
        first use, which on an offline or proxied box turns the page into a
        spinner. The manifest is small and WAL is already on, so a concurrent
        run is not blocked by this read.
        """
        import sqlite3

        path = self.config.manifest_path
        if not path.exists():
            return []
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            with conn:
                rows = conn.execute(
                    """SELECT run_id, batch_id, status, window_start, window_end, rows_written,
                              retry_count, started_at, finished_at, error_message
                       FROM ingestion_batches WHERE dataset = ?
                       ORDER BY COALESCE(started_at, '') DESC LIMIT ?""",
                    (dataset, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def dataset_detail(self, dataset: str) -> dict:
        """Everything the detail page shows, in one round trip."""
        from ashare_lake.domain.schemas import DATASET_SCHEMAS, PRIMARY_KEYS

        spec = DATASETS[dataset]
        row = next(r for r in self._rows() if r["dataset"] == dataset)
        parts = self.partitions(dataset)
        findings = self._health_findings()
        mine = [
            f
            for key in ("error_findings", "warning_findings")
            for f in findings.get(key, [])
            if f.get("dataset") == dataset
        ]

        return {
            **row,
            "layer": spec.layer,
            "partition_col": spec.partition_col,
            "max_staleness_days": spec.max_staleness_days,
            "backfill_chunk_days": spec.backfill_chunk_days,
            "backfill_chunk_symbols": getattr(spec, "backfill_chunk_symbols", None),
            # The source's own floor, not this lake's backlog: earlier windows
            # return nothing rather than less, and no backfill reaches past it.
            "earliest_available": spec.earliest_available(date.today()),
            "primary_key": PRIMARY_KEYS.get(dataset, []),
            "schema": [
                {"column": col, "dtype": str(dtype)}
                for col, dtype in DATASET_SCHEMAS.get(dataset, {}).items()
            ],
            # The per-partition series is not inlined: daily_bars alone is 6,202
            # rows, and the detail payload is loaded on every tab switch while
            # the series is only needed for one chart. `/partitions` serves it.
            "gaps": self._gaps(spec, parts),
            "findings": mine,
            "commands": self._commands(spec, row["freshness"]),
            "batches": self.recent_batches(dataset),
        }

    def provenance_series(self, dataset: str, *, max_buckets: int = 400) -> dict:
        """Source mix over time, bucketed to stay chartable.

        The collapsed :meth:`provenance` answers "which sources are in here";
        this answers "when did that change", which is where a routing switch or
        a mis-attributed backfill actually becomes visible.

        daily_bars alone has 11,324 (day, source) points — a megabyte of JSON to
        draw a few hundred pixels. Buckets widen until the series fits, and the
        chosen width is returned rather than applied silently: a caller that
        does not know it is looking at months cannot label the axis honestly.
        """
        stats = load_provenance_stats(self.config)
        partitions = load_partition_stats(self.config)
        empty = {"bucket": "day", "points": []}
        if stats.is_empty() or partitions.is_empty():
            return empty
        periods = partitions.filter(pl.col("dataset") == dataset).select(
            "partition", "period_start"
        )
        rows = stats.filter(pl.col("dataset") == dataset)
        if rows.is_empty() or periods.is_empty():
            return empty

        joined = rows.join(periods, on="partition", how="inner").filter(
            pl.col("period_start").is_not_null()
        )
        if joined.is_empty():
            return empty

        for bucket, expr in (
            ("day", pl.col("period_start")),
            ("month", pl.col("period_start").dt.truncate("1mo")),
            ("year", pl.col("period_start").dt.truncate("1y")),
        ):
            grouped = (
                joined.with_columns(expr.alias("period_start"))
                .group_by(["period_start", "source", "data_version"])
                .agg(pl.col("row_count").sum())
                .sort(["period_start", "source"])
            )
            if grouped.height <= max_buckets or bucket == "year":
                return {"bucket": bucket, "points": grouped.to_dicts()}
        return empty  # pragma: no cover — the year branch always returns

    def heatmap(self, *, days: int = 90) -> dict:
        """Coverage grid: one row per dataset, one cell per recent trading day.

        Cells answer "does a partition covering this day exist", which for a
        month/year-partitioned dataset is coarser than the day it is drawn on —
        the directory covers the period, and whether one particular session has
        rows in it is not knowable without reading the file. ``granularity``
        rides along on each row so a renderer can say so rather than imply a
        precision the layout does not have.
        """
        from ashare_lake.steps.common import _load_trading_calendar_df

        anchor = self.anchor()
        window_start = anchor - timedelta(days=int(days * 1.7) + 10)
        calendar = _load_trading_calendar_df(self.config, start=window_start, end=anchor)
        if calendar is None or calendar.is_empty():
            trading_days: list[date] = []
        else:
            trading_days = (
                calendar.filter(pl.col("is_trading"))
                .sort("trade_date")["trade_date"]
                .to_list()[-days:]
            )

        stats = load_partition_stats(self.config)
        spans: dict[str, list[tuple[date, date]]] = {}
        if not stats.is_empty():
            for row in stats.iter_rows(named=True):
                if row["period_start"] is None or row["period_end"] is None:
                    continue
                spans.setdefault(row["dataset"], []).append(
                    (row["period_start"], row["period_end"])
                )

        rows = []
        for row in self._rows():
            name = row["dataset"]
            intervals = sorted(spans.get(name, []))
            if row["granularity"] is None:
                cells = CELL_UNPARTITIONED * len(trading_days)
            elif not intervals:
                cells = CELL_OUTSIDE * len(trading_days)
            else:
                first, last = intervals[0][0], max(end for _, end in intervals)
                covered = set()
                for start, end in intervals:
                    covered.update(d for d in trading_days if start <= d <= end)
                cells = "".join(
                    CELL_COVERED
                    if day in covered
                    else (CELL_GAP if first <= day <= last else CELL_OUTSIDE)
                    for day in trading_days
                )
            rows.append(
                {
                    "dataset": name,
                    "tier": row["tier"],
                    "granularity": row["granularity"],
                    "freshness": row["freshness"],
                    # A gap only means "behind" for a dataset that publishes
                    # daily. northbound_holdings is quarterly, so most sessions
                    # inside its span legitimately have no partition, and
                    # drawing those as failures would cry wolf on every row.
                    "cadence_days": DATASETS[name].max_staleness_days,
                    "cells": cells,
                }
            )

        return {
            "days": trading_days,
            "legend": {
                CELL_COVERED: "covered",
                CELL_GAP: "gap",
                CELL_OUTSIDE: "outside coverage",
                CELL_UNPARTITIONED: "unpartitioned",
            },
            "rows": rows,
        }
