from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

from stock_data_engine.config import Config, WaveConfig
from stock_data_engine.orchestrator.deps import step_execution_levels, validate_steps_registered
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.orchestrator.registry import get_step
from stock_data_engine.steps.common import BACKFILL_START, is_trading_day

logger = logging.getLogger(__name__)


class JobEngine:
    """Wave-based ingestion orchestrator."""

    def __init__(self, config: Config):
        self.config = config
        self.manifest = Manifest(config.manifest_path)

    def run_job(
        self,
        job_name: str,
        trade_date: date | None = None,
        *,
        steps: list[str] | None = None,
        waves: list[WaveConfig] | None = None,
        backfill: bool = False,
        run_id: str | None = None,
        retry_failed_only: bool = False,
    ) -> dict[str, Any]:
        trade_date = trade_date or date.today()
        self.config._backfill = backfill

        if run_id and retry_failed_only:
            return self._retry_run(run_id, trade_date)

        if not backfill and job_name != "init" and not is_trading_day(self.config, trade_date):
            logger.info(
                "Skipping job %s: %s is not a trading day",
                job_name,
                trade_date.isoformat(),
            )
            skip_run_id = run_id or self.manifest.start_run(
                job_name,
                {"trade_date": trade_date.isoformat(), "backfill": backfill},
            )
            self.manifest.finish_run(skip_run_id, "skipped_non_trading_day")
            return {
                "run_id": skip_run_id,
                "status": "skipped_non_trading_day",
                "trade_date": trade_date.isoformat(),
            }

        metadata = {"trade_date": trade_date.isoformat(), "backfill": backfill}
        if not run_id:
            run_id = self.manifest.start_run(job_name, metadata)
        else:
            metadata.update(self.manifest.get_run_metadata(run_id))
            self.manifest.update_run_metadata(run_id, metadata)

        wave_list = waves or self.config.daily_waves
        if steps:
            wave_list = [WaveConfig(name="targeted", parallel=True, steps=steps)]

        all_steps = [name for wave in wave_list for name in wave.steps]
        validate_steps_registered(all_steps)

        context: dict[str, Any] = {"run_id": run_id, "trade_date": trade_date}
        results: list[dict[str, Any]] = []
        total_read = 0
        total_written = 0
        had_error = False

        for wave in wave_list:
            logger.info("Wave %s: %s (parallel=%s)", wave.name, wave.steps, wave.parallel)
            wave_results, wave_read, wave_written, wave_error = self._run_wave(
                wave, wave.steps, trade_date, run_id, context
            )
            results.extend(wave_results)
            total_read += wave_read
            total_written += wave_written
            had_error = had_error or wave_error

        status = "failed" if had_error else "success"
        self.manifest.finish_run(
            run_id,
            status,
            rows_read=total_read,
            rows_written=total_written,
            error_message="one or more steps failed" if had_error else None,
        )
        return {"run_id": run_id, "status": status, "results": results}

    def _run_wave(
        self,
        wave: WaveConfig,
        step_names: list[str],
        trade_date: date,
        run_id: str,
        context: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int, int, bool]:
        levels = step_execution_levels(step_names)
        results: list[dict[str, Any]] = []
        total_read = 0
        total_written = 0
        had_error = False
        context_lock = threading.Lock()

        def merge_result(result: dict[str, Any]) -> None:
            nonlocal total_read, total_written, had_error
            results.append(result)
            total_read += result.get("rows_read", 0)
            total_written += result.get("rows_written", 0)
            had_error = had_error or result.get("status") == "failed"
            updates = result.get("context_updates")
            if updates:
                with context_lock:
                    context.update(updates)

        if wave.parallel:
            for level in levels:
                if len(level) == 1:
                    merge_result(self._run_step(level[0], trade_date, run_id, context))
                    continue

                with ThreadPoolExecutor(max_workers=len(level)) as pool:
                    futures = {
                        pool.submit(self._run_step, name, trade_date, run_id, dict(context)): name
                        for name in level
                    }
                    for fut in as_completed(futures):
                        merge_result(fut.result())
        else:
            for level in levels:
                for name in level:
                    merge_result(self._run_step(name, trade_date, run_id, context))

        return results, total_read, total_written, had_error

    def _run_step(
        self, name: str, trade_date: date, run_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        entry = get_step(name)
        uses_worker_batches = entry.requires_workers
        batch_id = str(uuid.uuid4())
        if not uses_worker_batches:
            self.manifest.start_batch(run_id, batch_id, task_id=name, dataset=name)

        t0 = time.perf_counter()
        try:
            out = entry.fn(self.config, trade_date, run_id, context)
            elapsed = time.perf_counter() - t0
            if not uses_worker_batches:
                self.manifest.finish_batch(
                    run_id,
                    batch_id,
                    "success",
                    rows_read=out.get("rows_read", 0),
                    rows_written=out.get("rows_written", 0),
                )
            logger.info("Step %s OK in %.1fs (%s rows)", name, elapsed, out.get("rows_written", 0))
            return {
                "step": name,
                "status": "success",
                "elapsed": elapsed,
                **out,
            }
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            if not uses_worker_batches:
                self.manifest.finish_batch(
                    run_id,
                    batch_id,
                    "failed",
                    error_message=str(exc),
                )
            logger.exception("Step %s failed after %.1fs", name, elapsed)
            return {"step": name, "status": "failed", "error": str(exc), "elapsed": elapsed}

    def _retry_run(self, run_id: str, trade_date: date) -> dict[str, Any]:
        stale_marked = self.manifest.mark_stale_running_batches_failed(
            run_id,
            stale_after_seconds=self.config.batch_stale_seconds,
        )
        failed = self.manifest.get_failed_batches(run_id)
        if not failed:
            return {
                "run_id": run_id,
                "status": "success",
                "retried": 0,
                "stale_marked_failed": stale_marked,
            }

        context: dict[str, Any] = {"run_id": run_id, "trade_date": trade_date}
        context.update(self.manifest.get_run_metadata(run_id))

        worker_batches = [b for b in failed if b["dataset"] == "daily_bars"]
        step_batches = [b for b in failed if b["dataset"] != "daily_bars"]

        results: list[dict[str, Any]] = []

        if worker_batches:
            if any(
                b["window_start"] == BACKFILL_START.isoformat() for b in worker_batches
            ):
                self.config._backfill = True
            batch_specs = []
            for batch in worker_batches:
                symbols = json.loads(batch["symbols_json"] or "[]")
                batch_specs.append((batch["batch_id"], symbols))
            context["_retry_batch_specs"] = batch_specs
            results.append(self._run_step("daily_bars", trade_date, run_id, context))

        for batch in step_batches:
            dataset = batch["dataset"]
            results.append(self._run_step(dataset, trade_date, run_id, context))

        if self.manifest.incomplete_batch_count(run_id) == 0:
            result = self._run_step("compact", trade_date, run_id, context)
            updates = result.get("context_updates")
            if updates:
                context.update(updates)
            results.append(result)

        incomplete = self.manifest.incomplete_batch_count(run_id)
        status = "failed" if incomplete else "success"
        self.manifest.finish_run(run_id, status)
        return {
            "run_id": run_id,
            "status": status,
            "retried": len(failed),
            "stale_marked_failed": stale_marked,
            "results": results,
        }

    def run_init_phases(self, trade_date: date | None = None) -> dict[str, Any]:
        trade_date = trade_date or date.today()
        phases = self.config.init_phases or [
            "phase1_reference",
            "phase2a_corporate_actions",
            "phase2b_daily_bars_incremental",
            "phase2c_daily_bars_backfill",
            "phase3_index_and_status",
            "phase4_finalize",
        ]
        phase_steps = {
            "phase1_reference": ["instruments", "trading_calendar"],
            "phase2a_corporate_actions": ["corporate_actions"],
            "phase2b_daily_bars_incremental": ["daily_bars"],
            "phase2c_daily_bars_backfill": ["daily_bars"],
            "phase3_index_and_status": ["index_bars", "trading_status"],
            "phase4_finalize": ["compact", "derive_adj_factors", "audit"],
        }
        all_results = []
        run_id = self.manifest.start_run("init", {"phases": phases})
        for phase in phases:
            steps = phase_steps.get(phase, [])
            backfill = phase in (
                "phase2a_corporate_actions",
                "phase2c_daily_bars_backfill",
            )
            logger.info("Init phase %s: %s", phase, steps)
            result = self.run_job(
                "init",
                trade_date,
                steps=steps,
                backfill=backfill,
                run_id=run_id,
            )
            all_results.append({"phase": phase, **result})
        return {"run_id": run_id, "phases": all_results}
