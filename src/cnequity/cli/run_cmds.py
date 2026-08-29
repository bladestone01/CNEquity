"""The scheduled path: `run daily` and `retry`.

Composition of these into a day's worth of work lives in
`scripts/daily_pipeline.sh`, not here — the CLI runs one job, the script decides
which jobs a day needs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import (
    _cfg,
    _progress_logging,
    _run_status_exit_code,
    config_option,
)
from cnequity.cli.quality_cmds import _last_trading_day
from cnequity.config import WaveConfig
from cnequity.domain.market_time import shanghai_today
from cnequity.orchestrator.engine import JobEngine
from cnequity.orchestrator.run_lock import RunLockError


@cli.group()
def run():
    """Run scheduled jobs."""


def stale_fetch_steps(cfg, anchor: date) -> list[str]:
    """Registered fetch steps whose dataset is still behind *anchor*.

    Freshness is judged exactly as ``cne status --datasets`` judges it, so the
    two cannot disagree about what is behind.

    Derived datasets are excluded: they are recomputed by ``cne derive`` from
    curated inputs, and re-fetching is not what they need. Datasets with no
    registered step are excluded because there is nothing to run.
    """
    # Steps are registered by the module-level `import cnequity.steps`.
    from cnequity.domain.datasets import DATASETS, is_dataset_enabled, is_stale
    from cnequity.orchestrator.registry import STEP_REGISTRY
    from cnequity.query.reader import list_datasets

    out: list[str] = []
    for row in list_datasets(config=cfg).iter_rows(named=True):
        name = row["dataset"]
        spec = DATASETS[name]
        if spec.layer == "derived" or name not in STEP_REGISTRY:
            continue
        if not is_dataset_enabled(name, cfg):
            continue
        if not row["has_data"] or not row["watermarked"]:
            continue
        mark = row["watermark"] or row["coverage_end"]
        if is_stale(name, mark, anchor):
            out.append(name)
    return out


def _run_stale_only(cfg, engine, trade_date: date | None, *, backfill: bool) -> None:
    """Second attempt, same day, for whatever the first attempt did not land.

    The gap this closes: a ``snapshot`` dataset fetches only the run day, so a
    source outage during the one scheduled window loses that day permanently —
    ``valuation_metrics`` lost 2026-07-30 and 07-31 to a push2 clist outage and
    no later run could have recovered them. Per-host retries already exist and
    were exhausted; what was missing was a second window.
    """
    anchor = _last_trading_day(cfg, trade_date or shanghai_today())
    steps = stale_fetch_steps(cfg, anchor)
    if not steps:
        click.echo(f"nothing stale as of {anchor.isoformat()}")
        return
    click.echo(f"stale as of {anchor.isoformat()}: {', '.join(steps)}", err=True)
    try:
        result = engine.run_job(
            "daily:stale",
            trade_date=trade_date,
            waves=[WaveConfig(name="stale", parallel=False, steps=[*steps, "compact"])],
            backfill=backfill,
        )
    except RunLockError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"run_id": result["run_id"], "status": result["status"]}, indent=2))
    exit_code = _run_status_exit_code(result["status"])
    if exit_code:
        raise SystemExit(exit_code)


@run.command("daily")
@config_option
@click.option(
    "--group",
    "group_name",
    default=None,
    help=("Schedule group: core, capital, signals, fundamentals, macro_risk, research, intraday"),
)
@click.option(
    "--trade-date",
    "trade_date_str",
    default=None,
    help="As-of trade date YYYY-MM-DD (default: today). Use to catch up on weekends/holidays.",
)
@click.option("--backfill", is_flag=True)
@click.option("--quiet", is_flag=True, help="Only warnings and errors; no per-step progress.")
@click.option(
    "--stale-only",
    is_flag=True,
    help="Re-fetch only the datasets still behind the last trading day. "
    "Schedule a few hours after the main pipeline: a snapshot dataset that lost "
    "its window to a source outage cannot be replayed tomorrow.",
)
def run_daily(
    config_path: str,
    group_name: str | None,
    trade_date_str: str | None,
    backfill: bool,
    stale_only: bool,
    quiet: bool,
):
    """Run daily ingestion job (Wave DAG or schedule group)."""
    _progress_logging(quiet)
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    td = date.fromisoformat(trade_date_str) if trade_date_str else None
    if stale_only:
        if group_name:
            raise click.ClickException("--stale-only picks its own steps; drop --group.")
        _run_stale_only(cfg, engine, td, backfill=backfill)
        return
    try:
        if group_name:
            group = cfg.schedule_groups.get(group_name)
            if not group:
                raise click.ClickException(f"Unknown group: {group_name}")
            result = engine.run_job(
                f"daily:{group_name}",
                trade_date=td,
                waves=[WaveConfig(name=f"group:{group_name}", parallel=False, steps=group.steps)],
                backfill=backfill,
            )
        else:
            result = engine.run_job("daily", trade_date=td, backfill=backfill)
    except RunLockError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"run_id": result["run_id"], "status": result["status"]}, indent=2))
    # Exit non-zero on failure so schedulers (launchd/cron) and the daily
    # pipeline can detect it; a non-trading-day skip is a success (exit 0).
    exit_code = _run_status_exit_code(result["status"])
    if exit_code:
        raise SystemExit(exit_code)


def _retry_single_run(engine: JobEngine, run_id: str) -> dict:
    """Retry one run, print its result, and return it to the CLI caller."""
    run = engine.manifest.get_run(run_id)
    if run is None:
        raise click.ClickException(f"Unknown run_id: {run_id}")
    try:
        if run["job_name"] == "init":
            result = engine.resume_init(run_id=run_id)
        else:
            result = engine.run_job("retry", retry_failed_only=True, run_id=run_id)
    except RunLockError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2, default=str))
    return result


def _failed_daily_group_runs(engine: JobEngine) -> list[dict]:
    """Return the latest failed run of each ``daily:*`` group."""
    latest: dict[str, dict] = {}
    for row in engine.manifest.list_runs():
        run = dict(row)
        job_name = str(run["job_name"])
        if job_name.startswith("daily:"):
            # Manifest order is newest first; an older failure must not be
            # replayed once a newer run for that group has succeeded.
            latest.setdefault(job_name, run)
    return [latest[name] for name in sorted(latest) if latest[name]["status"] == "failed"]


@cli.command()
@config_option
@click.option("--run-id", default=None, help="Retry a specific run.")
@click.option(
    "--failed-groups",
    is_flag=True,
    help="Retry the latest failed run of each daily group.",
)
def retry(config_path: str, run_id: str | None, failed_groups: bool):
    """Retry one run or every latest failed daily group."""
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    if failed_groups:
        if run_id:
            raise click.ClickException("use either --run-id or --failed-groups, not both")
        runs = _failed_daily_group_runs(engine)
        if not runs:
            click.echo("No failed daily group run to retry.")
            return
        failed = False
        for run in runs:
            click.echo(f"Retrying failed daily group run {run['run_id']} ({run['job_name']})")
            # Heavy groups retain sizeable Polars/Python arenas. A fresh child
            # process per group releases that memory before the next retry.
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from cnequity.cli.main import cli; cli.main()",
                    "retry",
                    "--config",
                    config_path,
                    "--run-id",
                    str(run["run_id"]),
                ],
                check=False,
            )
            if proc.returncode != 0:
                failed = True
        if failed:
            raise SystemExit(1)
        return
    if not run_id:
        raise click.ClickException("provide --run-id or --failed-groups")
    result = _retry_single_run(engine, run_id)
    exit_code = _run_status_exit_code(str(result.get("status", "failed")))
    if exit_code:
        raise SystemExit(exit_code)
