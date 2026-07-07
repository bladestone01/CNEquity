from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from stock_data_engine.adapters.sina.adj_factors import fetch_adj_factor_series
from stock_data_engine.config import Config
from stock_data_engine.domain.rate_limit import wait_source
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.domain.symbols import is_cdr_symbol, parse_symbol
from stock_data_engine.storage.atomic import write_parquet_atomic

logger = logging.getLogger(__name__)

# Only hfq is persisted; qfq is derived at query time (ADR-0004).
STORED_ADJUST_TYPE = "hfq"

# Derive step fails when uncached fetch failures exceed this share of symbol×type tasks.
FAIL_RATIO_THRESHOLD = 0.05

# A single trading day's hfq factor step beyond this ratio cannot come from any real
# corporate action — even the largest historical splits step the factor by ~10x. Such a
# jump signals a factor break (a dropped/misaligned event date or a corrupt source series),
# so we surface it as a fail-loud finding. This is a coarse tripwire for gross corruption;
# it deliberately does not try to catch ~2x breaks, which are indistinguishable from a real
# 10-for-10 bonus at the factor level alone and are guarded downstream via raw-vs-adjusted
# return divergence.
MAX_FACTOR_STEP_RATIO = 20.0


class AdjFactorsFetchError(RuntimeError):
    """Raised when adj factor fetch fails and no cache is available."""


class AdjFactorsDeriveError(RuntimeError):
    """Raised when too many symbols lack adj factors after derive."""

    def __init__(self, message: str, *, findings: list[dict]):
        super().__init__(message)
        self.findings = findings


class AdjFactorsResult:
    __slots__ = ("rows", "task_count", "failed", "findings")

    def __init__(
        self,
        rows: int,
        task_count: int,
        failed: list[str],
        findings: list[dict],
    ) -> None:
        self.rows = rows
        self.task_count = task_count
        self.failed = failed
        self.findings = findings

    @property
    def fail_ratio(self) -> float:
        if not self.task_count:
            return 0.0
        return len(self.failed) / self.task_count


def _is_cdr(symbol: str) -> bool:
    try:
        info = parse_symbol(symbol)
    except ValueError:
        return False
    return is_cdr_symbol(info.code, info.exchange)


def _load_daily_bar_dates(config: Config) -> pl.DataFrame:
    bars_path = config.curated_root / "daily_bars"
    if not bars_path.exists():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date})

    bar_files = list(bars_path.glob("**/*.parquet"))
    if not bar_files:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date})

    bars = pl.concat([pl.read_parquet(f) for f in bar_files], how="diagonal_relaxed")
    return bars.select(["symbol", "trade_date"]).unique().sort(["symbol", "trade_date"])


def _align_factors_to_bars(
    sym_bars: pl.DataFrame,
    symbol: str,
    factors: pl.DataFrame,
    adjust_type: str,
) -> pl.DataFrame:
    sym_dates = sym_bars.select("trade_date").sort("trade_date")
    if sym_dates.is_empty():
        return pl.DataFrame()

    # Sina emits a sparse step function: one row per corporate-action date, with the
    # factor level that applies from that date forward. The factor on any trading day is
    # therefore the most recent event on or before it — an as-of (backward) join, not an
    # exact-date join. An exact join drops every event date that isn't itself a bar date
    # (e.g. all pre-history events when bars start long after IPO), leaving leading bars to
    # default to 1.0 and turning the first in-window event into a spurious >1000x jump.
    factors_sorted = factors.select(["trade_date", "factor"]).sort("trade_date")
    aligned = sym_dates.join_asof(factors_sorted, on="trade_date", strategy="backward")
    aligned = aligned.with_columns(pl.col("factor").fill_null(1.0))
    return aligned.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.lit(adjust_type).alias("adjust_type"),
    )


def _cache_path(config: Config, symbol: str, adjust_type: str) -> Path:
    cache_dir = config.meta_root / "adj_factors_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace(".", "_")
    return cache_dir / f"{safe}_{adjust_type}.parquet"


def _load_cache(config: Config, symbol: str, adjust_type: str) -> pl.DataFrame | None:
    path = _cache_path(config, symbol, adjust_type)
    if not path.exists():
        return None
    return pl.read_parquet(path).select(["trade_date", "factor"])


def _save_cache(config: Config, symbol: str, adjust_type: str, factors: pl.DataFrame) -> None:
    if factors.is_empty():
        return
    path = _cache_path(config, symbol, adjust_type)
    factors.write_parquet(path, compression="zstd")


def _read_parquet_files(files: list[Path]) -> pl.DataFrame:
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


def _corporate_action_symbols_on(config: Config, trade_date: date) -> set[str]:
    root = config.curated_root / "corporate_actions"
    if not root.exists():
        return set()

    part_files = list((root / f"ex_date={trade_date.isoformat()}").glob("**/*.parquet"))
    files = part_files or list(root.glob("**/*.parquet"))
    df = _read_parquet_files(files)
    if df.is_empty() or not {"symbol", "ex_date"}.issubset(df.columns):
        return set()
    today = df.filter(pl.col("ex_date") == trade_date)
    return set(today["symbol"].unique().to_list())


def _new_listing_symbols_on(config: Config, trade_date: date) -> set[str]:
    root = config.curated_root / "instruments"
    if not root.exists():
        return set()

    df = _read_parquet_files(list(root.glob("**/*.parquet")))
    if df.is_empty() or not {"symbol", "list_date"}.issubset(df.columns):
        return set()
    listed = df.filter(pl.col("list_date") == trade_date)
    return set(listed["symbol"].unique().to_list())


def _event_refresh_symbols(config: Config, trade_date: date) -> set[str]:
    """Symbols whose factor cache should be refreshed for this trading date."""
    return _corporate_action_symbols_on(config, trade_date) | _new_listing_symbols_on(
        config, trade_date
    )


def _needs_refresh(
    cached: pl.DataFrame | None,
    force: bool,
) -> bool:
    if force:
        return True
    if cached is None or cached.is_empty():
        return True
    return False


def _resolve_factors(
    config: Config,
    symbol: str,
    adjust_type: str,
    sym_bars: pl.DataFrame,
    *,
    force: bool,
    client: httpx.Client,
) -> pl.DataFrame | None:
    cached = _load_cache(config, symbol, adjust_type)
    if not _needs_refresh(cached, force):
        return cached

    source = config.adj_factors_source
    interval = config.source_intervals.get(source, 0.2)
    rate_state_dir = config.meta_root / "rate_limits"
    try:
        wait_source(rate_state_dir, source, interval)
        if source != "sina":
            logger.warning("Unknown adj_factors source %s; skipping %s", source, symbol)
            return cached
        factors = fetch_adj_factor_series(symbol, adjust_type, client=client)
        _save_cache(config, symbol, adjust_type, factors)
        return factors
    except Exception as exc:
        if cached is None or cached.is_empty():
            raise AdjFactorsFetchError(
                f"No cached adj factors for {symbol} ({adjust_type}): {exc}"
            ) from exc
        logger.warning("External adj factors failed for %s (%s): %s", symbol, adjust_type, exc)
        return cached


def _factor_continuity_findings(out: pl.DataFrame) -> list[dict]:
    """Flag symbols whose stored factor jumps beyond any plausible corporate action.

    hfq factors are a step function that moves only on ex-dates by a bounded ratio; a
    day-over-day jump above MAX_FACTOR_STEP_RATIO (or a symmetric collapse) means the
    aligned series is broken. Emits one finding per offending symbol at its worst step.
    """
    if out.is_empty() or out.height < 2:
        return []
    ratios = (
        out.sort(["symbol", "adjust_type", "trade_date"])
        .with_columns(
            (pl.col("factor") / pl.col("factor").shift(1))
            .over(["symbol", "adjust_type"])
            .alias("_step_ratio")
        )
        .filter(pl.col("_step_ratio").is_not_null() & (pl.col("factor") > 0))
        .filter(
            (pl.col("_step_ratio") > MAX_FACTOR_STEP_RATIO)
            | (pl.col("_step_ratio") < 1.0 / MAX_FACTOR_STEP_RATIO)
        )
    )
    if ratios.is_empty():
        return []

    ratios = ratios.with_columns(
        pl.max_horizontal(
            pl.col("_step_ratio"), (1.0 / pl.col("_step_ratio"))
        ).alias("_severity")
    )
    worst = (
        ratios.sort("_severity", descending=True)
        .group_by(["symbol", "adjust_type"], maintain_order=True)
        .first()
    )
    findings: list[dict] = []
    for row in worst.iter_rows(named=True):
        td = row["trade_date"]
        td_str = td.isoformat() if isinstance(td, date) else str(td)
        findings.append(
            {
                "dataset": "adj_factors",
                "severity": "error",
                "check": "adj_factor_continuity",
                "message": (
                    f"{row['symbol']} ({row['adjust_type']}) factor jumps "
                    f"{row['_step_ratio']:.1f}x on {td_str} to {row['factor']:.4g} "
                    f"(>{MAX_FACTOR_STEP_RATIO:.0f}x); likely a factor break, not a "
                    f"corporate action"
                ),
                "symbol": row["symbol"],
                "adjust_type": row["adjust_type"],
                "trade_date": td_str,
            }
        )
    return findings


def _fetch_failure_finding(symbol: str, adjust_type: str, exc: Exception) -> dict:
    return {
        "dataset": "adj_factors",
        "severity": "error",
        "check": "adj_factor_fetch_failed",
        "message": f"No cached adj factors for {symbol} ({adjust_type}): {exc}",
        "symbol": symbol,
        "adjust_type": adjust_type,
    }


def _process_symbol_adj(
    config: Config,
    sym: str,
    adj: str,
    sym_bars: pl.DataFrame,
    *,
    force: bool,
    client: httpx.Client | None = None,
) -> tuple[pl.DataFrame | None, str | None, dict | None]:
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=20.0)
    try:
        try:
            factors = _resolve_factors(config, sym, adj, sym_bars, force=force, client=client)
        except AdjFactorsFetchError as exc:
            return None, f"{sym}:{adj}", _fetch_failure_finding(sym, adj, exc)
        if factors is None or factors.is_empty():
            return None, None, None
        aligned = _align_factors_to_bars(sym_bars, sym, factors, adj)
        if aligned.is_empty():
            return None, None, None
        return aligned, None, None
    finally:
        if own_client:
            client.close()


def compute_adj_factors(
    config: Config,
    adjust_type: str | None = None,
    *,
    refresh_symbols: list[str] | None = None,
) -> AdjFactorsResult:
    bars = _load_daily_bar_dates(config)
    if bars.is_empty():
        return AdjFactorsResult(0, 0, [], [])

    adjust_types = [adjust_type] if adjust_type else list(config.adj_factors_types)
    skipped = [t for t in adjust_types if t != STORED_ADJUST_TYPE]
    if skipped:
        logger.warning(
            "adj_factors: ignoring non-persisted adjust_types %s (only %s is stored; "
            "derive qfq via load(..., adjust='qfq') — ADR-0004)",
            skipped,
            STORED_ADJUST_TYPE,
        )
    adjust_types = [STORED_ADJUST_TYPE]
    latest_trade_date = bars["trade_date"].max()
    refresh_set = set(refresh_symbols or [])
    if isinstance(latest_trade_date, date):
        refresh_set |= _event_refresh_symbols(config, latest_trade_date)
    workers = max(1, min(config.workers, 16))

    tasks: list[tuple[str, str, pl.DataFrame, bool]] = []
    skipped_cdr: list[str] = []
    for group in bars.partition_by("symbol"):
        sym = group["symbol"][0]
        if _is_cdr(sym):
            skipped_cdr.append(sym)
            continue
        sym_bars = group.select("trade_date").sort("trade_date")
        force = sym in refresh_set
        for adj in adjust_types:
            tasks.append((sym, adj, sym_bars, force))
    if skipped_cdr:
        logger.info(
            "adj_factors: skipping %d CDR symbol(s) %s — sina has no CDR factor "
            "coverage and all_a excludes CDRs; loads report adj_is_exact=False",
            len(skipped_cdr),
            skipped_cdr,
        )

    frames: list[pl.DataFrame] = []
    failed: list[str] = []
    findings: list[dict] = []

    if workers <= 1 or len(tasks) == 1:
        with httpx.Client(timeout=20.0) as client:
            for sym, adj, sym_bars, force in tasks:
                aligned, fail_key, finding = _process_symbol_adj(
                    config, sym, adj, sym_bars, force=force, client=client
                )
                if fail_key:
                    failed.append(fail_key)
                if finding:
                    findings.append(finding)
                if aligned is not None:
                    frames.append(aligned)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _process_symbol_adj,
                    config,
                    sym,
                    adj,
                    sym_bars,
                    force=force,
                )
                for sym, adj, sym_bars, force in tasks
            ]
            for fut in as_completed(futures):
                aligned, fail_key, finding = fut.result()
                if fail_key:
                    failed.append(fail_key)
                if finding:
                    findings.append(finding)
                if aligned is not None:
                    frames.append(aligned)

    if not frames:
        return AdjFactorsResult(0, len(tasks), failed, findings)

    out = pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["symbol", "trade_date", "adjust_type"], keep="last"
    )
    findings.extend(_factor_continuity_findings(out))
    out = with_provenance(out, source=config.adj_factors_source, data_version="v1")

    total = 0
    for key, group in out.partition_by("trade_date", as_dict=True).items():
        td = key[0] if isinstance(key, tuple) else key
        td_str = td.isoformat() if isinstance(td, date) else str(td)
        out_dir = config.derived_root / "adj_factors" / f"trade_date={td_str}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "part-0.parquet"
        write_parquet_atomic(path, group, compression="zstd")
        total += group.height
    return AdjFactorsResult(total, len(tasks), failed, findings)
