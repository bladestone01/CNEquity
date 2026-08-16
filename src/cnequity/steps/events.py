"""L2 corporate-event steps: corporate_actions, announcement_index,
earnings_disclosure_schedule."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from cnequity.adapters.cninfo.announcements import fetch_announcement_index
from cnequity.adapters.eastmoney.corporate_actions import fetch_corporate_actions_eastmoney
from cnequity.adapters.eastmoney.earnings_disclosure import (
    _backfill_report_dates,
    fetch_earnings_disclosure_schedule,
)
from cnequity.adapters.tdx_protocol.client import fetch_corporate_actions
from cnequity.config import Config
from cnequity.domain.schemas import with_provenance
from cnequity.orchestrator.registry import register_step
from cnequity.quality.failover import (
    snapshot_corporate_actions_backup,
    snapshot_corporate_actions_tdx_backup,
)
from cnequity.steps.common import (
    BACKFILL_START,
    fetch_incremental_daily,
    load_symbols,
    write_simple,
)
from cnequity.steps.http_common import run_incremental_fetched, write_fetched

# TDX xdxr is per-symbol (backfill); EastMoney datacenter supports ex-date filter (daily).
_CANONICAL_BACKFILL = "tdx_protocol"
_CANONICAL_DAILY = "eastmoney"


logger = logging.getLogger(__name__)


@register_step("corporate_actions", group="core", depends_on=["instruments"])
def step_corporate_actions(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    rl = config.tdx_rate_limit_spec()
    backfill = getattr(config, "_backfill", False)
    findings: list[dict] = []

    if backfill:
        symbols = load_symbols(config)
        if config.failover_enabled:
            # Best-effort: this writes an EastMoney snapshot for cross-source
            # audit, not the canonical rows. It must never decide whether the
            # backfill runs — when EastMoney changed its filter grammar the
            # raise from here aborted the whole step before TDX, the actual
            # primary, was contacted at all.
            try:
                snapshot_corporate_actions_backup(
                    config, trade_date=trade_date, run_id=run_id, backfill=True
                )
            except Exception as exc:  # noqa: BLE001 — audit artifact, not the data
                logger.warning(
                    "corporate_actions: backup snapshot failed (%s: %s); "
                    "continuing with the canonical TDX fetch",
                    type(exc).__name__,
                    exc,
                )
        df = fetch_corporate_actions(
            trade_date,
            symbols=symbols,
            backfill=True,
            rate_limit=rl,
            allow_mock=config.tdx_allow_mock,
            primary_only=True,
            config=config,
        )
        canonical_source = _CANONICAL_BACKFILL
    else:
        if not config.sources.get("eastmoney", True):
            raise RuntimeError("corporate_actions daily: eastmoney source disabled in config")
        df, findings = fetch_incremental_daily(
            config,
            "corporate_actions",
            trade_date,
            lambda d: fetch_corporate_actions_eastmoney(d, backfill=False, config=config),
            allow_empty=True,
            date_col="ex_date",
        )
        canonical_source = _CANONICAL_DAILY
        if config.failover_enabled and df.height:
            ex_today = df.filter(pl.col("ex_date") == trade_date)
            if ex_today.height:
                snapshot_corporate_actions_tdx_backup(
                    config,
                    trade_date=trade_date,
                    symbols=ex_today["symbol"].unique().to_list(),
                    run_id=run_id,
                    rate_limit=rl,
                )

    if backfill and not df.is_empty():
        start = getattr(config, "_backfill_start", None) or BACKFILL_START
        end = getattr(config, "_backfill_end", None) or trade_date
        if "ex_date" not in df.columns:
            raise RuntimeError("corporate_actions: backfill response has no ex_date column")
        parsed_dates = df.get_column("ex_date").cast(pl.Date, strict=False)
        invalid = (
            parsed_dates.is_null()
            | (parsed_dates < start).fill_null(False)
            | (parsed_dates > end).fill_null(False)
        )
        if int(invalid.sum()):
            raise RuntimeError(
                "corporate_actions: backfill response returned row(s) outside "
                f"requested window {start.isoformat()}..{end.isoformat()}"
            )
        df = df.with_columns(parsed_dates.alias("ex_date"))

    context_updates: dict = {"symbols_to_rebackfill": []}
    if findings:
        context_updates["audit_findings"] = findings
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0, "context_updates": context_updates}

    df = with_provenance(df, source=canonical_source, data_version="v1")

    rebackfill: list[str] = []
    if df.height and "symbol" in df.columns and "ex_date" in df.columns:
        today = df.filter(pl.col("ex_date") == trade_date)
        if today.height:
            rebackfill = today["symbol"].unique().to_list()

    context_updates["symbols_to_rebackfill"] = rebackfill
    result = write_simple(config, run_id, "corporate_actions", df)
    result["context_updates"] = context_updates
    return result


@register_step("earnings_disclosure_schedule", group="fundamentals", depends_on=["instruments"])
def step_earnings_disclosure_schedule(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("earnings_disclosure_schedule: eastmoney source disabled in config")
    # Period-keyed like financial_statement_items (watermark=False): daily runs
    # refresh the open disclosure windows; backfill walks every period 2016+.
    backfill = getattr(config, "_backfill", False)
    df = fetch_earnings_disclosure_schedule(trade_date, backfill=backfill, config=config)
    missing_periods: set[str] = set()
    if backfill:
        expected = {
            f"{period[:4]}Q{(int(period[5:7]) - 1) // 3 + 1}"
            for period in _backfill_report_dates(
                trade_date,
                start=getattr(config, "_backfill_start", None),
                end=getattr(config, "_backfill_end", None),
            )
        }
        observed = (
            set(df.get_column("report_period").drop_nulls().to_list())
            if not df.is_empty() and "report_period" in df.columns
            else set()
        )
        missing_periods = expected - observed
    if backfill and missing_periods:
        result: dict
        if df.is_empty():
            result = {"rows_read": 0, "rows_written": 0}
        else:
            result = write_fetched(
                config, run_id, "earnings_disclosure_schedule", df, source="eastmoney"
            )
        result["status"] = "warning"
        result["missing_periods"] = len(missing_periods)
        result["context_updates"] = {
            "audit_findings": [
                {
                    "dataset": "earnings_disclosure_schedule",
                    "severity": "warning",
                    "check": "backfill_missing_report_periods",
                    "message": (
                        f"earnings disclosure schedule missing {len(missing_periods)} "
                        f"requested report period(s): {', '.join(sorted(missing_periods)[:8])}"
                    ),
                    "missing_periods": sorted(missing_periods),
                }
            ]
        }
        return result
    if df.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    return write_fetched(config, run_id, "earnings_disclosure_schedule", df, source="eastmoney")


@register_step("announcement_index", group="capital", depends_on=["instruments"])
def step_announcement_index(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("cninfo", True):
        raise RuntimeError("announcement_index: cninfo source disabled in config")
    if getattr(config, "_backfill", False):
        from cnequity.steps.common import walk_day_backfill

        return walk_day_backfill(
            config,
            trade_date,
            run_id,
            "announcement_index",
            lambda d: fetch_announcement_index(d, config=config),
            source="cninfo",
            date_col="announce_date",
            floor=date(2010, 1, 1),
        )
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "announcement_index",
        lambda d: fetch_announcement_index(d, config=config),
        source="cninfo",
        date_col="announce_date",
    )
