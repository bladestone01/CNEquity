"""Failover helpers — write backup snapshots without touching curated (ADR-0003)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.bars import fetch_daily_bars as fetch_em_daily_bars
from stock_data_engine.adapters.eastmoney.corporate_actions import fetch_corporate_actions_eastmoney
from stock_data_engine.config import Config, FailoverDatasetSpec
from stock_data_engine.domain.schemas import with_provenance
from stock_data_engine.storage.source_snapshots import SnapshotStore

logger = logging.getLogger(__name__)


def failover_spec(config: Config, dataset: str) -> FailoverDatasetSpec | None:
    if not config.failover_enabled:
        return None
    for spec in config.failover_datasets:
        if spec.name == dataset:
            return spec
    return None


def write_backup_snapshot(
    config: Config,
    dataset: str,
    df: pl.DataFrame,
    *,
    run_id: str,
    batch_id: str,
    source: str,
    trade_date: date | None = None,
) -> None:
    if df.is_empty():
        return
    path = SnapshotStore(config.meta_root).write(
        dataset,
        df,
        source=source,
        data_version="v1",
        run_id=run_id,
        batch_id=batch_id,
        trade_date=trade_date,
    )
    if path:
        logger.info(
            "Wrote backup snapshot %s source=%s rows=%s → %s",
            dataset,
            source,
            df.height,
            path,
        )


def snapshot_daily_bars_backup(
    config: Config,
    *,
    symbols: list[str],
    start: date,
    end: date,
    run_id: str,
    batch_id: str,
) -> None:
    spec = failover_spec(config, "daily_bars")
    if spec is None or not config.sources.get(spec.backup, True):
        return
    config.rate_limit(spec.backup)
    df = fetch_em_daily_bars(symbols, start, end)
    if df.is_empty():
        return
    df = with_provenance(df, source=spec.backup, data_version="v1")
    write_backup_snapshot(
        config,
        "daily_bars",
        df,
        run_id=run_id,
        batch_id=batch_id,
        source=spec.backup,
        trade_date=end,
    )


def snapshot_corporate_actions_backup(
    config: Config,
    *,
    trade_date: date,
    run_id: str,
    backfill: bool,
) -> None:
    """Write EastMoney rows to snapshot (used when TDX is backfill canonical)."""
    if not backfill:
        return
    spec = failover_spec(config, "corporate_actions")
    if spec is None or not config.sources.get(spec.backup, True):
        return
    config.rate_limit(spec.backup)
    df = fetch_corporate_actions_eastmoney(trade_date, backfill=backfill)
    if df.is_empty():
        return
    df = with_provenance(df, source=spec.backup, data_version="v1")
    write_backup_snapshot(
        config,
        "corporate_actions",
        df,
        run_id=run_id,
        batch_id="backup",
        source=spec.backup,
        trade_date=trade_date,
    )


def snapshot_corporate_actions_tdx_backup(
    config: Config,
    *,
    trade_date: date,
    symbols: list[str],
    run_id: str,
    rate_limit,
) -> None:
    """Snapshot TDX xdxr for ex-date symbols when EastMoney is daily canonical."""
    spec = failover_spec(config, "corporate_actions")
    if spec is None or not symbols or not config.tdx_enabled:
        return
    from stock_data_engine.adapters.tdx_protocol.client import _quotes_client
    from stock_data_engine.adapters.tdx_protocol.corporate_actions import fetch_corporate_actions_tdx

    tdx_df = fetch_corporate_actions_tdx(
        symbols,
        trade_date=trade_date,
        backfill=False,
        client_factory=_quotes_client,
        rate_limit=rate_limit,
    )
    if tdx_df.is_empty():
        return
    tdx_df = with_provenance(tdx_df, source=spec.backup, data_version="v1")
    write_backup_snapshot(
        config,
        "corporate_actions",
        tdx_df,
        run_id=run_id,
        batch_id="tdx-backup",
        source=spec.backup,
        trade_date=trade_date,
    )
