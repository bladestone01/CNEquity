from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import click
import polars as pl

import stock_data_engine.steps  # noqa: F401 — register steps
from stock_data_engine.config import WaveConfig, load_config, validate_config
from stock_data_engine.derive.adj_factors import compute_adj_factors
from stock_data_engine.domain.datasets import fetch_semantics, get_dataset
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.orchestrator.run_lock import RunLockError
from stock_data_engine.quality.audit import run_audit
from stock_data_engine.query.on_demand import OnDemandService
from stock_data_engine.query.views import ensure_duckdb_views
from stock_data_engine.steps.finalize import step_compact
from stock_data_engine.storage.layout import init_data_layout
from stock_data_engine.storage.staging_cleanup import clean_staging

USER_CONFIG = "configs/stockdata.toml"
EXAMPLE_CONFIG = "configs/stockdata.example.toml"
DEFAULT_CONFIG = USER_CONFIG


def resolve_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if config_path == USER_CONFIG and not path.exists():
        example = Path(EXAMPLE_CONFIG)
        if example.exists():
            raise click.ClickException(
                f"Config not found: {USER_CONFIG}. "
                f"Copy {EXAMPLE_CONFIG} to {USER_CONFIG} and edit data.root."
            )
    if not path.exists():
        raise click.ClickException(f"Config not found: {path}")
    return path


def _cfg(config: str):
    return load_config(resolve_config_path(config))


@click.group()
@click.version_option(package_name="stock-data-engine")
def cli():
    """StockDataEngine — A-share data ingestion CLI."""


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--layout-only",
    is_flag=True,
    help="Only create directories, manifest, and DuckDB views (skip init phases).",
)
@click.option(
    "--trade-date",
    default=None,
    help="As-of trade date for init phases (YYYY-MM-DD); default today.",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Resume the latest incomplete init run (retry failed batches + missing phases).",
)
@click.option(
    "--run-id",
    "resume_run_id",
    default=None,
    help="Resume a specific init run_id (implies --resume).",
)
@click.option(
    "--keep-going",
    is_flag=True,
    help="Continue init phases after a phase failure instead of stopping.",
)
def init(
    config_path: str,
    layout_only: bool,
    trade_date: str | None,
    resume: bool,
    resume_run_id: str | None,
    keep_going: bool,
):
    """Initialize data lake and run configured init phases (first full backfill)."""
    cfg = _cfg(config_path)
    init_data_layout(cfg)
    if layout_only:
        click.echo(f"Initialized layout at {cfg.data_root}")
        return

    td = date.fromisoformat(trade_date) if trade_date else date.today()
    engine = JobEngine(cfg)

    if not resume and not resume_run_id:
        incomplete = engine.manifest.latest_incomplete_init_run()
        if incomplete is not None:
            raise click.ClickException(
                f"Incomplete init run {incomplete['run_id']} exists "
                f"(status={incomplete['status']}). "
                "Use `sde init --resume` or `sde retry --run-id "
                f"{incomplete['run_id']}` — do not start a new full init."
            )

    result = engine.run_init_phases(
        trade_date=td,
        resume=resume or bool(resume_run_id),
        resume_run_id=resume_run_id,
        keep_going=keep_going,
    )
    click.echo(json.dumps(result, indent=2, default=str))
    if result.get("status") != "success":
        raise SystemExit(1)


@cli.command("config")
@click.argument("action", type=click.Choice(["validate"]))
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def config_cmd(action: str, config_path: str):
    """Validate configuration."""
    cfg = _cfg(config_path)
    errors = validate_config(cfg)
    if errors:
        for e in errors:
            click.echo(f"ERROR: {e}", err=True)
        raise SystemExit(1)
    click.echo("Configuration OK")


@cli.group()
def run():
    """Run scheduled jobs."""


@run.command("daily")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--group", "group_name", default=None, help="Schedule group: core, capital, signals")
@click.option("--backfill", is_flag=True)
def run_daily(config_path: str, group_name: str | None, backfill: bool):
    """Run daily ingestion job (Wave DAG or schedule group)."""
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    try:
        if group_name:
            group = cfg.schedule_groups.get(group_name)
            if not group:
                raise click.ClickException(f"Unknown group: {group_name}")
            result = engine.run_job(
                f"daily:{group_name}",
                waves=[WaveConfig(name=f"group:{group_name}", parallel=False, steps=group.steps)],
                backfill=backfill,
            )
        else:
            result = engine.run_job("daily", backfill=backfill)
    except RunLockError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"run_id": result["run_id"], "status": result["status"]}, indent=2))
    # Exit non-zero on failure so schedulers (launchd/cron) and the daily
    # pipeline can detect it; a non-trading-day skip is a success (exit 0).
    if result["status"] not in ("success", "skipped_non_trading_day"):
        raise SystemExit(1)


@cli.command()
@click.argument("dataset")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--retry-failed",
    is_flag=True,
    help="Resume sector_bars backfill (skip boards already written to checkpoint).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Clear sector_bars backfill checkpoint and re-fetch all boards.",
)
@click.option(
    "--start",
    "start_str",
    default=None,
    help="Range start (YYYY-MM-DD) for date-walking backfills (margin_trading).",
)
@click.option(
    "--end",
    "end_str",
    default=None,
    help="Range end (YYYY-MM-DD) for date-walking backfills (margin_trading).",
)
@click.option(
    "--workers",
    default=1,
    show_default=True,
    help="Concurrent fetch workers for date-walking backfills; each worker is "
    "throttled to 1 req/s (aggregate up to N req/s, bypassing the source limiter).",
)
def backfill(
    dataset: str,
    config_path: str,
    retry_failed: bool,
    force: bool,
    start_str: str | None,
    end_str: str | None,
    workers: int,
):
    """Backfill a dataset."""
    # Multi-hour sweeps (baostock ST, EM sector kline) need visible progress on
    # stdout; adapters log at INFO. Keep WARNING+ for third-party noise.
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if fetch_semantics(dataset) == "snapshot" and not get_dataset(dataset).backfill_source:
        raise click.ClickException(
            f"{dataset}: backfill not supported — fetch semantics are snapshot "
            "(live page stamped with trade_date; historical values unavailable). "
            "Run daily ingestion on trading days instead."
        )
    cfg = _cfg(config_path)
    if dataset == "sector_bars":
        if retry_failed and force:
            raise click.ClickException("Use either --retry-failed or --force, not both.")
        cfg._sector_bars_force = force
    if start_str:
        cfg._backfill_start = date.fromisoformat(start_str)
    if end_str:
        cfg._backfill_end = date.fromisoformat(end_str)
    cfg._backfill_workers = workers
    engine = JobEngine(cfg)
    result = engine.run_job("backfill", steps=[dataset], backfill=True)
    if result["status"] == "success":
        compact_out = step_compact(cfg, date.today(), result["run_id"], {})
        result["compact"] = compact_out
    click.echo(json.dumps(result, indent=2, default=str))
    if result["status"] != "success":
        raise SystemExit(1)


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--run-id", default=None)
def compact(config_path: str, run_id: str | None):
    """Compact staging into curated for all datasets staged in the run."""
    cfg = _cfg(config_path)
    manifest = Manifest(cfg.manifest_path)
    if not run_id:
        latest = manifest.latest_run()
        if not latest:
            raise click.ClickException("No runs found")
        run_id = latest["run_id"]

    out = step_compact(cfg, date.today(), run_id, {})
    click.echo(
        json.dumps(
            {"run_id": run_id, "rows_written": out.get("rows_written", 0), **out},
            indent=2,
            default=str,
        )
    )


@cli.command()
@click.argument("name", default="adj_factors")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def derive(name: str, config_path: str):
    """Derive computed datasets."""
    cfg = _cfg(config_path)
    if name == "adj_factors":
        result = compute_adj_factors(cfg)
        click.echo(f"Derived {name}: {result.rows} rows")
        if result.failed:
            click.echo(
                f"Warnings: {len(result.failed)} symbol×type fetch failures "
                f"({result.fail_ratio:.1%})",
                err=True,
            )
    elif name == "trading_status":
        from stock_data_engine.derive.trading_status_history import derive_suspension_history

        rows = derive_suspension_history(cfg)
        click.echo(f"Derived historical suspension: {rows} rows into trading_status")
    elif name == "sector_routing":
        from stock_data_engine.derive.sector_routing import derive_sector_routing

        summary = derive_sector_routing(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "sector_code_map":
        from stock_data_engine.derive.sector_code_map import derive_sector_code_map

        summary = derive_sector_code_map(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "valuation_orphans":
        from stock_data_engine.storage.valuation_orphans import purge_valuation_orphan_symbols

        summary = purge_valuation_orphan_symbols(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    else:
        raise click.ClickException(f"Unknown derive target: {name}")


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--run-id", default=None)
@click.option(
    "--full",
    "full",
    is_flag=True,
    help="Whole-lake health snapshot (current state + freshness), not a per-run file.",
)
def audit(config_path: str, run_id: str | None, full: bool):
    """Run quality audit, or --full for a current whole-lake health snapshot."""
    cfg = _cfg(config_path)

    if full:
        from stock_data_engine.quality.audit import lake_health

        health = lake_health(cfg, date.today())
        sev = health["findings_by_severity"]
        click.echo(f"Lake health @ last trading day {health['last_trading_day']}")
        click.echo(
            f"  findings: {sev.get('error', 0)} error, "
            f"{sev.get('warning', 0)} warning, {sev.get('info', 0)} info"
        )
        if health["empty_datasets"]:
            click.echo(f"  empty datasets: {', '.join(health['empty_datasets'])}")
        if health["stale_datasets"]:
            click.echo(f"  STALE datasets: {', '.join(health['stale_datasets'])}")
        for f in health["error_findings"]:
            click.echo(f"  [error]   {f.get('dataset', ''):22} {f.get('message', '')}")
        for f in health["warning_findings"]:
            click.echo(f"  [warning] {f.get('dataset', ''):22} {f.get('message', '')}")
        click.echo("HEALTHY" if health["healthy"] else "UNHEALTHY")
        if not health["healthy"]:
            raise SystemExit(1)
        return

    manifest = Manifest(cfg.manifest_path)
    latest = manifest.latest_run() if not run_id else None
    rid = run_id or (latest["run_id"] if latest else "manual")

    n = run_audit(cfg, rid, date.today())
    click.echo(f"Audit complete: {n} findings written")


def _last_trading_day(cfg, today: date) -> date:
    from datetime import timedelta

    from stock_data_engine.steps.common import is_trading_day

    d = today
    for _ in range(15):
        if is_trading_day(cfg, d):
            return d
        d -= timedelta(days=1)
    return today


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--datasets",
    "show_datasets",
    is_flag=True,
    help="Per-dataset freshness: coverage, watermark, and staleness vs the last trading day.",
)
def status(config_path: str, show_datasets: bool):
    """Show latest run status, or per-dataset freshness with --datasets."""
    cfg = _cfg(config_path)

    if show_datasets:
        import polars as pl_mod

        from stock_data_engine.domain.datasets import is_stale
        from stock_data_engine.query.reader import list_datasets

        anchor = _last_trading_day(cfg, date.today())
        df = list_datasets(config=cfg)

        def _freshness(row: dict) -> str:
            if not row["has_data"]:
                return "empty"
            # Datasets keyed by report_period (no daily watermark) are not
            # judged on a daily cadence.
            if not row["watermarked"]:
                return "n/a"
            mark = row["watermark"] or row["coverage_end"]
            # Per-dataset tolerance (T+1, quarterly …) — inherent lag is not STALE.
            return "STALE" if is_stale(row["dataset"], mark, anchor) else "fresh"

        df = df.with_columns(
            pl_mod.Series(
                "freshness", [_freshness(r) for r in df.iter_rows(named=True)]
            )
        )
        click.echo(f"last trading day: {anchor.isoformat()}")
        with pl_mod.Config(tbl_rows=-1, tbl_cols=-1, fmt_str_lengths=32):
            click.echo(df)
        stale = df.filter(pl_mod.col("freshness") == "STALE").height
        if stale:
            click.echo(f"\n{stale} dataset(s) STALE — check runs with `sde status` / `sde retry`.")
            raise SystemExit(1)
        return

    manifest = Manifest(cfg.manifest_path)
    latest = manifest.latest_run()
    if not latest:
        click.echo("No runs yet.")
        return
    summary = manifest.run_summary(latest["run_id"])
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--run-id", required=True)
def retry(config_path: str, run_id: str):
    """Retry failed batches and missing init steps for a run."""
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
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
    if result.get("status") not in ("success",):
        raise SystemExit(1)


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--dry-run", is_flag=True, help="Report removable staging without deleting.")
@click.option(
    "--orphan-retention-days",
    default=7,
    show_default=True,
    help="Delete manifest-less orphan staging older than this many days.",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Also delete staging of failed/incomplete runs. Their success batches "
        "are demoted to failed so `sde retry` refetches them (data is refetched, "
        "not lost, but the retry becomes a full re-run)."
    ),
)
@click.option(
    "--reconcile-runs",
    is_flag=True,
    help="Mark runs stuck in 'running' (crashed workers) as failed before cleanup.",
)
@click.option(
    "--reconcile-after-seconds",
    default=300,
    show_default=True,
    help="Only reconcile runs idle longer than this many seconds.",
)
def clean(
    config_path: str,
    dry_run: bool,
    orphan_retention_days: int,
    force: bool,
    reconcile_runs: bool,
    reconcile_after_seconds: int,
):
    """Remove staging for successful compacted runs and aged orphans.

    Failed/incomplete runs keep their staging (it is resumable state) unless
    --force is given.
    """
    cfg = _cfg(config_path)
    reconciled: dict[str, int] | None = None
    if reconcile_runs:
        manifest = Manifest(cfg.manifest_path)
        reconciled = manifest.reconcile_orphaned_runs(
            stale_after_seconds=float(reconcile_after_seconds)
        )
    result = clean_staging(
        cfg,
        dry_run=dry_run,
        orphan_retention_days=orphan_retention_days,
        force=force,
    )
    click.echo(
        json.dumps(
            {
                "dry_run": dry_run,
                "reconciled": reconciled,
                "removed_run_ids": result.removed_run_ids,
                "orphan_run_ids": result.orphan_run_ids,
                "force_removed_run_ids": result.force_removed_run_ids,
                "skipped_run_ids": result.skipped_run_ids,
                "bytes_freed": result.bytes_freed,
            },
            indent=2,
        )
    )


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def catalog(config_path: str):
    """List datasets and latest partition info."""
    cfg = _cfg(config_path)
    curated = cfg.curated_root
    entries = []
    if curated.exists():
        for ds_dir in sorted(curated.iterdir()):
            if ds_dir.is_dir():
                files = list(ds_dir.glob("**/*.parquet"))
                # lazy count(*) resolves from parquet metadata without
                # decoding data pages — cheap even on a 10-year lake.
                rows = (
                    int(
                        pl.scan_parquet([str(f) for f in files])
                        .select(pl.len())
                        .collect()
                        .item()
                    )
                    if files
                    else 0
                )
                entries.append({"dataset": ds_dir.name, "files": len(files), "rows": rows})
    click.echo(json.dumps(entries, indent=2))


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--sql", default="SELECT COUNT(*) AS n FROM daily_bars")
@click.option("--dataset", default=None, help="On-demand dataset name")
@click.option("--symbol", default=None, help="Symbol for on-demand fetch")
def query(config_path: str, sql: str, dataset: str | None, symbol: str | None):
    """Run DuckDB SQL or on-demand dataset fetch."""
    cfg = _cfg(config_path)
    if dataset and symbol:
        svc = OnDemandService(cfg)
        data = svc.fetch(dataset, symbol)
        click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return
    db_path = ensure_duckdb_views(cfg)
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(sql).pl()
        click.echo(df)
    finally:
        con.close()


@cli.command("servers")
@click.argument("action", type=click.Choice(["test"]))
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def servers(action: str, config_path: str):
    """Test TDX server connectivity."""
    try:
        from stock_data_engine.adapters.tdx_protocol.client import _quotes_client

        cfg = _cfg(config_path)
        client = _quotes_client(cfg)
        _ = client
        click.echo("TDX connection OK")
    except ImportError:
        click.echo("mootdx not installed — install with: pip install -e '.[tdx]'")
    except Exception as exc:
        click.echo(f"TDX connection failed: {exc}", err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli()
