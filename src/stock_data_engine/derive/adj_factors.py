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
from stock_data_engine.storage.atomic import write_parquet_atomic

logger = logging.getLogger(__name__)

# Only hfq is persisted; qfq is derived at query time (ADR-0004).
STORED_ADJUST_TYPE = "hfq"

# Derive step fails when uncached fetch failures exceed this share of symbol×type tasks.
FAIL_RATIO_THRESHOLD = 0.05


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

    aligned = sym_dates.join(factors, on="trade_date", how="left").sort("trade_date")
    aligned = aligned.with_columns(pl.col("factor").forward_fill().fill_null(1.0))
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
    for group in bars.partition_by("symbol"):
        sym = group["symbol"][0]
        sym_bars = group.select("trade_date").sort("trade_date")
        force = sym in refresh_set
        for adj in adjust_types:
            tasks.append((sym, adj, sym_bars, force))

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
