"""L7 rotation steps: hot rank, sector bars/flows, market news headlines."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import date, timedelta

from stock_data_engine.adapters.eastmoney.rotation import (
    fetch_hot_rank,
    fetch_news_headlines,
    fetch_sector_bars,
    fetch_sector_fund_flow,
)
from stock_data_engine.config import Config
from stock_data_engine.orchestrator.registry import register_step
from stock_data_engine.steps.http_common import run_incremental_fetched, write_fetched

# Board kline history depth for `sde backfill sector_bars` — enough for the
# workbench's sector momentum / RRG lookbacks with a year of slack.
_SECTOR_BARS_BACKFILL_DAYS = 400
_SECTOR_BARS_BACKFILL_STATE = "sector_bars_backfill"
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
    return _run_rotation_step(config, trade_date, run_id, "sector_bars", fetch_sector_bars)


def _sector_bars_backfill_state_path(config: Config):
    return config.meta_root / "state" / f"{_SECTOR_BARS_BACKFILL_STATE}.json"


def _sector_bars_completed(config: Config) -> set[str]:
    path = _sector_bars_backfill_state_path(config)
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")).get("completed", []))


def clear_sector_bars_backfill_state(config: Config) -> None:
    path = _sector_bars_backfill_state_path(config)
    if path.exists():
        path.unlink()
    # Drop hybrid-era split checkpoints so a fresh EM sweep starts clean.
    for track in ("tdx", "em"):
        hybrid = config.meta_root / "state" / f"sector_bars_backfill_{track}.json"
        if hybrid.exists():
            hybrid.unlink()


def _mark_sector_bars_completed(config: Config, sector_codes: list[str]) -> None:
    path = _sector_bars_backfill_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = sorted(_sector_bars_completed(config) | set(sector_codes))
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
    """Historical board bars via the EastMoney kline API (the daily clist
    snapshot only sees today). Partial sweeps surface as an audit finding."""
    from stock_data_engine.adapters.eastmoney.rotation import fetch_sector_bars_history

    if not config.sources.get("eastmoney", True):
        raise RuntimeError("sector_bars: eastmoney source disabled in config")
    if getattr(config, "_sector_bars_force", False):
        clear_sector_bars_backfill_state(config)

    start = trade_date - timedelta(days=_SECTOR_BARS_BACKFILL_DAYS)
    completed = _sector_bars_completed(config)
    df, failed, succeeded = fetch_sector_bars_history(
        start,
        trade_date,
        config=config,
        skip_sectors=completed,
    )
    attempted = len(succeeded) + len(failed)
    if attempted == 0:
        return {
            "rows_read": 0,
            "rows_written": 0,
            "note": "all boards already sector_bars-backfilled",
        }

    result: dict = {"rows_read": 0, "rows_written": 0}
    if not df.is_empty():
        result = write_fetched(config, run_id, "sector_bars", df, source="eastmoney")

    if succeeded:
        _mark_sector_bars_completed(config, succeeded)

    if failed:
        result["failed_sectors"] = len(failed)
        result["attempted_sectors"] = attempted
        finding = {
            "dataset": "sector_bars",
            "severity": "warning",
            "code": "sector_bars_backfill_incomplete",
            "message": (
                f"{len(failed)}/{attempted} board(s) failed the kline history sweep; "
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
