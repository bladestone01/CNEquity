"""L6 macro + L8 risk batch steps."""

from __future__ import annotations

from datetime import date

import polars as pl

from ashare_lake.adapters.cninfo.regulatory import fetch_regulatory_events
from ashare_lake.adapters.eastmoney.share_unlock import fetch_share_unlock_schedule
from ashare_lake.adapters.macro.indicators import fetch_macro_indicators
from ashare_lake.config import Config
from ashare_lake.derive.market_breadth import compute_market_breadth
from ashare_lake.orchestrator.registry import register_step
from ashare_lake.quality.macro_checks import macro_revision_findings
from ashare_lake.steps.common import BACKFILL_START
from ashare_lake.steps.http_common import run_incremental_fetched


@register_step("macro_indicators", group="macro_risk")
def step_macro_indicators(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    # Revisions have to be detected here, between fetch and write: compact keeps
    # only the newest row per (indicator_id, obs_date), so once the write lands
    # the previous published value is gone. The overwrite itself is deliberate —
    # it is what lets a corrected history heal on the next run without a
    # migration (issue #3) — so this records the change rather than blocking it.
    revisions: list[dict] = []

    def _fetch(day: date):
        df = fetch_macro_indicators(day, config=config)
        revisions.extend(macro_revision_findings(config, df, day))
        return df

    result = run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "macro_indicators",
        _fetch,
        # The adapter stamps `source` per row (EastMoney and the PBOC both feed
        # this dataset), and with_provenance keeps a pre-set column. This value
        # only applies to the empty-frame case.
        source="eastmoney",
        allow_empty=True,
    )
    if revisions:
        updates = result.setdefault("context_updates", {})
        updates["audit_findings"] = [*(updates.get("audit_findings") or []), *revisions]
    return result


@register_step("market_breadth", group="macro_risk", depends_on=["daily_bars"])
def step_market_breadth(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if getattr(config, "_backfill", False):
        from ashare_lake.steps.common import walk_day_backfill

        # Pure local computation from daily_bars — no network, no rate limit,
        # so the floor is daily_bars' own start rather than a probed vendor date.
        return walk_day_backfill(
            config,
            trade_date,
            run_id,
            "market_breadth",
            lambda d: compute_market_breadth(config, d),
            source="derived",
            floor=date(2001, 1, 1),
        )
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "market_breadth",
        lambda d: compute_market_breadth(config, d),
        source="derived",
        allow_empty=True,
    )


@register_step("share_unlock_schedule", group="macro_risk", depends_on=["instruments"])
def step_share_unlock_schedule(
    config: Config, trade_date: date, run_id: str, context: dict
) -> dict:
    if not config.sources.get("eastmoney", True):
        raise RuntimeError("share_unlock_schedule: eastmoney source disabled in config")
    if getattr(config, "_backfill", False):
        return _backfill_share_unlock_schedule(config, trade_date, run_id)
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "share_unlock_schedule",
        fetch_share_unlock_schedule,
        source="eastmoney",
        allow_empty=True,
    )


_UNLOCK_HORIZON_DAYS = 180
# Under the horizon so consecutive windows overlap — a 180-day stride would
# leave a one-day crack an unlock could fall through if a period boundary
# landed exactly wrong; 150 leaves 30 days of slack on both sides.
_UNLOCK_STRIDE_DAYS = 150


def _backfill_share_unlock_schedule(config: Config, trade_date: date, run_id: str) -> dict:
    """Walk in ~150-day strides, not daily.

    Each call returns every unlock in the next 180 days from *its* date — PK is
    (symbol, unlock_date), no snapshot/as-of column at all, so it is not a
    per-day PIT series to replay. A daily walk would re-fetch the same event
    up to ~180 times before it aged out of the window; striding under the
    horizon covers the same ground once, with 30 days of overlap as a margin
    against an unlock landing exactly on a stride boundary.
    """
    from datetime import timedelta

    from ashare_lake.domain.schemas import data_version_for, with_provenance
    from ashare_lake.storage import StagingWriter

    start = getattr(config, "_backfill_start", None) or BACKFILL_START
    end = getattr(config, "_backfill_end", None) or trade_date
    cursor = start
    frames = []
    rows_written = 0
    while cursor <= end:
        config.rate_limit("eastmoney")
        df = fetch_share_unlock_schedule(cursor, horizon_days=_UNLOCK_HORIZON_DAYS)
        if not df.is_empty():
            frames.append(df)
        cursor += timedelta(days=_UNLOCK_STRIDE_DAYS)
    if not frames:
        return {"rows_read": 0, "rows_written": 0}
    part = with_provenance(
        pl.concat(frames, how="diagonal_relaxed"),
        source="eastmoney",
        data_version=data_version_for("share_unlock_schedule"),
    )
    StagingWriter(config.staging_root).write_batch("share_unlock_schedule", run_id, "bf-0000", part)
    rows_written = part.height
    return {"rows_read": rows_written, "rows_written": rows_written}


@register_step("regulatory_events", group="macro_risk", depends_on=["instruments"])
def step_regulatory_events(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    if not config.sources.get("cninfo", True):
        raise RuntimeError("regulatory_events: cninfo source disabled in config")
    if getattr(config, "_backfill", False):
        from ashare_lake.steps.common import walk_day_backfill

        return walk_day_backfill(
            config,
            trade_date,
            run_id,
            "regulatory_events",
            lambda d: fetch_regulatory_events(d, config=config),
            source="cninfo",
            date_col="event_date",
            floor=date(2010, 1, 1),
        )
    return run_incremental_fetched(
        config,
        trade_date,
        run_id,
        "regulatory_events",
        lambda d: fetch_regulatory_events(d, config=config),
        source="cninfo",
        allow_empty=True,
    )
