from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from typing import Any

from stock_data_engine.adapters.tdx_protocol.client import fetch_daily_bars, normalize_with_source
from stock_data_engine.config import Config
from stock_data_engine.domain.rate_limit import RateLimitSpec
from stock_data_engine.storage import StagingWriter

logger = logging.getLogger(__name__)


def _worker_fetch_batch(args: tuple) -> dict[str, Any]:
    symbols, start_iso, end_iso, staging_root, dataset, run_id, batch_id, rate_limit = args
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    rl = RateLimitSpec(*rate_limit) if rate_limit else None
    df = fetch_daily_bars(symbols, start, end, rate_limit=rl)
    df = normalize_with_source(df)
    writer = StagingWriter(staging_root)
    writer.write_batch(dataset, run_id, batch_id, df)
    return {"rows_read": df.height, "rows_written": df.height, "batch_id": batch_id}


def fetch_daily_bars_parallel(
    config: Config,
    symbols: list[str],
    start: date,
    end: date,
    run_id: str,
    dataset: str = "daily_bars",
) -> dict[str, Any]:
    if not symbols:
        return {"rows_read": 0, "rows_written": 0}

    batch_size = config.batch_size
    batches = [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]
    staging_root = config.staging_root
    total_read = 0
    total_written = 0
    rl = config.tdx_rate_limit_spec()
    rate_limit_tuple = (rl.state_dir, rl.source, rl.min_interval) if rl else None

    if config.workers <= 1 or len(batches) == 1:
        writer = StagingWriter(staging_root)
        for i, batch in enumerate(batches):
            df = fetch_daily_bars(batch, start, end, rate_limit=rl)
            df = normalize_with_source(df)
            writer.write_batch(dataset, run_id, f"batch-{i}", df)
            total_read += df.height
            total_written += df.height
        return {"rows_read": total_read, "rows_written": total_written}

    tasks = []
    for i, batch in enumerate(batches):
        tasks.append(
            (
                batch,
                start.isoformat(),
                end.isoformat(),
                str(staging_root),
                dataset,
                run_id,
                f"batch-{i}",
                rate_limit_tuple,
            )
        )

    with ProcessPoolExecutor(max_workers=min(config.workers, len(tasks))) as pool:
        futures = [pool.submit(_worker_fetch_batch, t) for t in tasks]
        for fut in as_completed(futures):
            result = fut.result()
            total_read += result["rows_read"]
            total_written += result["rows_written"]

    return {"rows_read": total_read, "rows_written": total_written}
