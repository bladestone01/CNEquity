from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from typing import Any

from stock_data_engine.adapters.tdx_protocol.client import fetch_daily_bars, normalize_with_source
from stock_data_engine.config import Config
from stock_data_engine.domain.rate_limit import RateLimitSpec
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.storage import StagingWriter

logger = logging.getLogger(__name__)


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
    ) = args
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
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

    try:
        df = fetch_daily_bars(symbols, start, end, rate_limit=rl, allow_mock=allow_mock)
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
        raise


def fetch_daily_bars_parallel(
    config: Config,
    symbols: list[str],
    start: date,
    end: date,
    run_id: str,
    dataset: str = "daily_bars",
    *,
    batch_specs: list[tuple[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Fetch daily bars in symbol batches; each batch is recorded in manifest."""
    if batch_specs:
        batches = batch_specs
    elif not symbols:
        return {"rows_read": 0, "rows_written": 0}
    else:
        batch_size = config.batch_size
        batches = [
            (f"batch-{i}", symbols[i : i + batch_size])
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

    def _run_batch(batch_id: str, batch_symbols: list[str]) -> dict[str, Any]:
        manifest.start_batch(
            run_id,
            batch_id,
            task_id=dataset,
            dataset=dataset,
            symbols=batch_symbols,
            window_start=start.isoformat(),
            window_end=end.isoformat(),
        )
        try:
            df = fetch_daily_bars(
                batch_symbols,
                start,
                end,
                rate_limit=rl,
                allow_mock=config.tdx_allow_mock,
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
            raise

    if config.workers <= 1 or len(batches) == 1:
        had_error = False
        for batch_id, batch_symbols in batches:
            try:
                result = _run_batch(batch_id, batch_symbols)
                total_read += result["rows_read"]
                total_written += result["rows_written"]
            except Exception:
                had_error = True
        if had_error:
            raise RuntimeError(f"{dataset}: one or more symbol batches failed")
        return {"rows_read": total_read, "rows_written": total_written}

    tasks = []
    for batch_id, batch_symbols in batches:
        tasks.append(
            (
                batch_symbols,
                start.isoformat(),
                end.isoformat(),
                str(staging_root),
                dataset,
                run_id,
                batch_id,
                rate_limit_tuple,
                config.tdx_allow_mock,
                manifest_path,
            )
        )

    had_error = False
    with ProcessPoolExecutor(max_workers=min(config.workers, len(tasks))) as pool:
        futures = [pool.submit(_worker_fetch_batch, t) for t in tasks]
        for fut in as_completed(futures):
            try:
                result = fut.result()
                total_read += result["rows_read"]
                total_written += result["rows_written"]
            except Exception:
                had_error = True

    if had_error:
        raise RuntimeError(f"{dataset}: one or more symbol batches failed")
    return {"rows_read": total_read, "rows_written": total_written}
