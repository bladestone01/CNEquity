"""L7 rotation steps: hot rank, sector bars/flows, market news headlines."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import date, timedelta

import polars as pl

from stock_data_engine.adapters.eastmoney.rotation import (
    fetch_hot_rank,
    fetch_news_headlines,
    fetch_sector_fund_flow,
)
from stock_data_engine.adapters.hybrid.sector_bars import fetch_hybrid_sector_bars
from stock_data_engine.config import Config
from stock_data_engine.derive.sector_routing import OHLC_EM, OHLC_TDX, load_sector_routing
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import run_incremental_fetched, write_fetched

# Board kline history depth for `sde backfill sector_bars` — enough for the
# workbench's sector momentum / RRG lookbacks with a year of slack.
_SECTOR_BARS_BACKFILL_DAYS = 400
_SECTOR_BARS_BACKFILL_LEGACY = "sector_bars_backfill"
_SECTOR_BARS_BACKFILL_TRACKS = ("tdx", "em")
_SECTOR_BARS_FAILURE_THRESHOLD = 0.5


def _run_rotation_step(
    config: Config,
    trade_date: date,
    run_id: str,
    dataset: str,
    fetch_fn,
    *,
    allow_empty: bool = True,
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError(f"{dataset}: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        dataset,
        fetch_fn,
        source="eastmoney",
        allow_empty=allow_empty,
    )


@register_step("hot_rank", group="research", depends_on=["instruments"])
def step_hot_rank(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(config, trade_date, run_id, "hot_rank", fetch_hot_rank)


@register_step("sector_bars", group="research", depends_on=["instruments"])
def step_sector_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        return _backfill_sector_bars(config, trade_date, run_id)
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("sector_bars: eastmoney source disabled in config")
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "sector_bars",
        lambda td: fetch_hybrid_sector_bars(td, config=config),
        source="hybrid",
    )


def _sector_bars_backfill_state_path(config: Config, track: str) -> os.PathLike:
    return config.meta_root / "state" / f"sector_bars_backfill_{track}.json"


def _sector_bars_legacy_state_path(config: Config) -> os.PathLike:
    return config.meta_root / "state" / f"{_SECTOR_BARS_BACKFILL_LEGACY}.json"


def _sector_bars_completed(config: Config, track: str) -> set[str]:
    path = _sector_bars_backfill_state_path(config, track)
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")).get("completed", []))
    if track == "em":
        legacy = _sector_bars_legacy_state_path(config)
        if legacy.exists():
            return set(json.loads(legacy.read_text(encoding="utf-8")).get("completed", []))
    return set()


def clear_sector_bars_backfill_state(config: Config) -> None:
    for track in _SECTOR_BARS_BACKFILL_TRACKS:
        path = _sector_bars_backfill_state_path(config, track)
        if path.exists():
            path.unlink()
    legacy = _sector_bars_legacy_state_path(config)
    if legacy.exists():
        legacy.unlink()


def _mark_sector_bars_completed(config: Config, track: str, sector_codes: list[str]) -> None:
    path = _sector_bars_backfill_state_path(config, track)
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = sorted(_sector_bars_completed(config, track) | set(sector_codes))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"completed": completed}, handle, indent=2)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _backfill_sector_bars(config: Config, trade_date: date, run_id: str) -> dict:
    """Historical board bars via TDX (routed) + EastMoney kline (EM-only boards).

    Requires ``meta/sector_ohlc_routing.parquet`` from ``sde derive sector_routing``.
    Partial sweeps surface as an audit finding per track."""
    from stock_data_engine.adapters.eastmoney.rotation import fetch_sector_bars_history
    from stock_data_engine.adapters.tdx_protocol.sector_bars import fetch_sector_index_bars_batch

    if not config.sources.get("eastmoney", True):
        raise RuntimeError("sector_bars: eastmoney source disabled in config")

    routing = load_sector_routing(config)
    if routing.is_empty():
        raise RuntimeError(
            "sector_bars backfill requires meta/sector_ohlc_routing.parquet; "
            "run `sde derive sector_routing` first"
        )

    if getattr(config, "_sector_bars_force", False):
        clear_sector_bars_backfill_state(config)

    start = trade_date - timedelta(days=_SECTOR_BARS_BACKFILL_DAYS)
    tdx_completed = _sector_bars_completed(config, "tdx")
    em_completed = _sector_bars_completed(config, "em")
    em_sectors = set(
        routing.filter(pl.col("ohlc_source") == OHLC_EM)["sector_code"].to_list()
    )

    tdx_df = pl.DataFrame()
    tdx_failed: list[str] = []
    tdx_succeeded: list[str] = []
    if config.sources.get("tdx", True):
        tdx_df, tdx_failed, tdx_succeeded = fetch_sector_index_bars_batch(
            routing,
            start,
            trade_date,
            config=config,
            skip_sectors=tdx_completed,
            backfill=True,
        )

    em_df, em_failed, em_succeeded = fetch_sector_bars_history(
        start,
        trade_date,
        config=config,
        skip_sectors=em_completed,
        only_sectors=em_sectors,
    )

    attempted = len(tdx_succeeded) + len(tdx_failed) + len(em_succeeded) + len(em_failed)
    if attempted == 0:
        return {
            "rows_read": 0,
            "rows_written": 0,
            "note": "all boards already sector_bars-backfilled",
        }

    parts: list[pl.DataFrame] = []
    if not tdx_df.is_empty():
        parts.append(tdx_df.with_columns(pl.lit(OHLC_TDX).alias("source")))
    if not em_df.is_empty():
        parts.append(em_df.with_columns(pl.lit(OHLC_EM).alias("source")))
    df = pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()

    result: dict = {"rows_read": 0, "rows_written": 0}
    if not df.is_empty():
        result = write_fetched(config, run_id, "sector_bars", df, source="hybrid")

    if tdx_succeeded:
        _mark_sector_bars_completed(config, "tdx", tdx_succeeded)
    if em_succeeded:
        _mark_sector_bars_completed(config, "em", em_succeeded)

    failed = tdx_failed + em_failed
    if failed:
        result["failed_sectors"] = len(failed)
        result["attempted_sectors"] = attempted
        result["tdx_failed"] = len(tdx_failed)
        result["em_failed"] = len(em_failed)
        finding = {
            "dataset": "sector_bars",
            "severity": "warning",
            "code": "sector_bars_backfill_incomplete",
            "message": (
                f"{len(failed)}/{attempted} board(s) failed the hybrid history sweep "
                f"(tdx={len(tdx_failed)}, em={len(em_failed)}); "
                "re-run `sde backfill sector_bars --retry-failed` to resume."
            ),
        }
        result.setdefault("context_updates", {})["audit_findings"] = [finding]
        if attempted and len(failed) / attempted > _SECTOR_BARS_FAILURE_THRESHOLD:
            result["status"] = "warning"

    return result


@register_step("sector_fund_flow", group="research", depends_on=["instruments"])
def step_sector_fund_flow(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(
        config, trade_date, run_id, "sector_fund_flow", fetch_sector_fund_flow
    )


@register_step("news_headlines", group="research")
def step_news_headlines(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    return _run_rotation_step(
        config, trade_date, run_id, "news_headlines", fetch_news_headlines, allow_empty=True
    )
