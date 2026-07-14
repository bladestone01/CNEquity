"""L7 rotation steps: hot rank, sector bars/flows, market news headlines."""

from __future__ import annotations

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


def _backfill_sector_bars(config: Config, trade_date: date, run_id: str) -> dict:
    """Historical board bars via the EastMoney kline API (the daily clist
    snapshot only sees today). Partial sweeps surface as an audit finding."""
    from stock_data_engine.adapters.eastmoney.rotation import fetch_sector_bars_history

    if not config.sources.get("eastmoney", True):
        raise RuntimeError("sector_bars: eastmoney source disabled in config")
    start = trade_date - timedelta(days=_SECTOR_BARS_BACKFILL_DAYS)
    df, failed = fetch_sector_bars_history(start, trade_date)
    result: dict = {"rows_read": 0, "rows_written": 0}
    if not df.is_empty():
        result = write_fetched(config, run_id, "sector_bars", df, source="eastmoney")
    if failed:
        result["failed_sectors"] = len(failed)
        result.setdefault("context_updates", {})["audit_findings"] = [
            {
                "dataset": "sector_bars",
                "severity": "warning",
                "code": "sector_bars_backfill_incomplete",
                "message": (
                    f"{len(failed)} board(s) failed the kline history sweep; "
                    "re-run `sde backfill sector_bars` to retry."
                ),
            }
        ]
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
