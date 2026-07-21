from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from datetime import date
from pathlib import Path
from typing import Any

from ashare_lake.adapters.tdx_protocol.client import fetch_daily_bars, normalize_with_source
from ashare_lake.config import Config, load_config
from ashare_lake.domain.rate_limit import RateLimitSpec
from ashare_lake.orchestrator.manifest import Manifest
from ashare_lake.quality.failover import snapshot_daily_bars_backup
from ashare_lake.steps.common import BACKFILL_START
from ashare_lake.storage import StagingWriter

logger = logging.getLogger(__name__)

# (batch_id, symbols, window_start, window_end)
BatchSpec = tuple[str, list[str], date, date]


def _symbol_batch_id(start: date, end: date, index: int) -> str:
    """Unique batch id per symbol chunk and fetch window within a run."""
    return f"{start.isoformat()}_{end.isoformat()}-batch-{index}"


def _worker_tdx_config(
    config_path: str,
    staging_root: str,
    *,
    allow_mock: bool,
    backfill: bool,
) -> Config:
    if config_path:
        cfg = load_config(Path(config_path))
    else:
        cfg = Config(data_root=Path(staging_root).parent)
    cfg.tdx_allow_mock = allow_mock
    cfg._backfill = backfill
    return cfg


def _window_backfill(start: date) -> bool:
    return start == BACKFILL_START


def _worker_fetch_batch(args: tuple) -> dict[str, Any]:
    (
        symbols,
        start_iso,
        end_iso,
        staging_root,
        dataset,
        run_id,
        batch_id,
        rate_limit,
        allow_mock,
        manifest_path,
        failover_enabled,
        backfill,
        config_path,
    ) = args
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    staging_root = Path(staging_root)
    rl = RateLimitSpec(*rate_limit) if rate_limit else None
    manifest = Manifest(manifest_path) if manifest_path else None

    if manifest:
        manifest.start_batch(
            run_id,
            batch_id,
            task_id=dataset,
            dataset=dataset,
            symbols=symbols,
            window_start=start_iso,
            window_end=end_iso,
        )

    tdx_cfg = _worker_tdx_config(
        config_path, staging_root, allow_mock=allow_mock, backfill=backfill
    )

    def _heartbeat() -> None:
        if manifest:
            manifest.touch_batch_heartbeat(run_id, batch_id)

    try:
        _heartbeat()
        df = fetch_daily_bars(
            symbols,
            start,
            end,
            rate_limit=rl,
            allow_mock=allow_mock,
            backfill=backfill,
            config=tdx_cfg,
            on_heartbeat=_heartbeat,
        )
        df = normalize_with_source(df)
        writer = StagingWriter(staging_root)
        writer.write_batch(dataset, run_id, batch_id, df)
        if manifest:
            manifest.finish_batch(
                run_id,
                batch_id,
                "success",
                rows_read=df.height,
                rows_written=df.height,
            )
        return {"rows_read": df.height, "rows_written": df.height, "batch_id": batch_id}
    except Exception as exc:
        if manifest:
            manifest.finish_batch(run_id, batch_id, "failed", error_message=str(exc))
        if failover_enabled and dataset == "daily_bars":
            from ashare_lake.adapters.eastmoney.bars import fetch_daily_bars as fetch_em_bars
            from ashare_lake.domain.schemas import with_provenance
            from ashare_lake.storage.source_snapshots import SnapshotStore

            backup_df = fetch_em_bars(symbols, start, end)
            if backup_df.height:
                backup_df = with_provenance(backup_df, source="eastmoney", data_version="v1")
                SnapshotStore(Path(staging_root).parent / "meta").write(
                    dataset,
                    backup_df,
                    source="eastmoney",
                    data_version="v1",
                    run_id=run_id,
                    batch_id=f"{batch_id}-backup",
                    trade_date=end,
                )
        raise


def fetch_daily_bars_parallel(
    config: Config,
    symbols: list[str],
    start: date,
    end: date,
    run_id: str,
    dataset: str = "daily_bars",
    *,
    batch_specs: list[BatchSpec] | None = None,
) -> dict[str, Any]:
    """Fetch daily bars in symbol batches; each batch is recorded in manifest."""
    if batch_specs:
        batches = batch_specs
    elif not symbols:
        return {"rows_read": 0, "rows_written": 0}
    else:
        batch_size = config.batch_size
        batches = [
            (_symbol_batch_id(start, end, i), symbols[i : i + batch_size], start, end)
            for i in range(0, len(symbols), batch_size)
        ]

    if not batches:
        return {"rows_read": 0, "rows_written": 0}

    staging_root = config.staging_root
    manifest_path = str(config.manifest_path)
    manifest = Manifest(config.manifest_path)
    total_read = 0
    total_written = 0
    rl = config.tdx_rate_limit_spec()
    rate_limit_tuple = (rl.state_dir, rl.source, rl.min_interval) if rl else None
    stale_seconds = config.batch_stale_seconds

    def _run_batch(
        batch_id: str,
        batch_symbols: list[str],
        batch_start: date,
        batch_end: date,
    ) -> dict[str, Any]:
        backfill = _window_backfill(batch_start)
        manifest.start_batch(
            run_id,
            batch_id,
            task_id=dataset,
            dataset=dataset,
            symbols=batch_symbols,
            window_start=batch_start.isoformat(),
            window_end=batch_end.isoformat(),
        )
        try:

            def _heartbeat() -> None:
                manifest.touch_batch_heartbeat(run_id, batch_id)

            _heartbeat()
            df = fetch_daily_bars(
                batch_symbols,
                batch_start,
                batch_end,
                rate_limit=rl,
                allow_mock=config.tdx_allow_mock,
                backfill=backfill,
                config=config,
                on_heartbeat=_heartbeat,
            )
            df = normalize_with_source(df)
            writer = StagingWriter(staging_root)
            writer.write_batch(dataset, run_id, batch_id, df)
            manifest.finish_batch(
                run_id,
                batch_id,
                "success",
                rows_read=df.height,
                rows_written=df.height,
            )
            return {"rows_read": df.height, "rows_written": df.height, "batch_id": batch_id}
        except Exception as exc:
            manifest.finish_batch(run_id, batch_id, "failed", error_message=str(exc))
            if config.failover_enabled and dataset == "daily_bars":
                snapshot_daily_bars_backup(
                    config,
                    symbols=batch_symbols,
                    start=batch_start,
                    end=batch_end,
                    run_id=run_id,
                    batch_id=f"{batch_id}-backup",
                )
            raise

    if config.workers <= 1 or len(batches) == 1:
        had_error = False
        for batch_id, batch_symbols, batch_start, batch_end in batches:
            try:
                result = _run_batch(batch_id, batch_symbols, batch_start, batch_end)
                total_read += result["rows_read"]
                total_written += result["rows_written"]
            except Exception:
                had_error = True
        if had_error:
            raise RuntimeError(f"{dataset}: one or more symbol batches failed")
        return {"rows_read": total_read, "rows_written": total_written}

    futures: dict = {}
    with ProcessPoolExecutor(max_workers=min(config.workers, len(batches))) as pool:
        for batch_id, batch_symbols, batch_start, batch_end in batches:
            backfill = _window_backfill(batch_start)
            task = (
                batch_symbols,
                batch_start.isoformat(),
                batch_end.isoformat(),
                str(staging_root),
                dataset,
                run_id,
                batch_id,
                rate_limit_tuple,
                config.tdx_allow_mock,
                manifest_path,
                config.failover_enabled,
                backfill,
                str(config.config_path) if config.config_path else "",
            )
            futures[pool.submit(_worker_fetch_batch, task)] = batch_id

        had_error = False
        for fut in as_completed(futures):
            batch_id = futures[fut]
            try:
                result = fut.result(timeout=stale_seconds)
                total_read += result["rows_read"]
                total_written += result["rows_written"]
            except TimeoutError:
                had_error = True
                manifest.mark_batch_stale(
                    run_id,
                    batch_id,
                    f"worker result timeout after {stale_seconds}s",
                )
                logger.warning(
                    "%s batch %s timed out after %ss; marked stale",
                    dataset,
                    batch_id,
                    stale_seconds,
                )
            except Exception as exc:
                had_error = True
                logger.warning("%s batch %s failed: %s", dataset, batch_id, exc)

    if had_error:
        raise RuntimeError(f"{dataset}: one or more symbol batches failed")
    return {"rows_read": total_read, "rows_written": total_written}
