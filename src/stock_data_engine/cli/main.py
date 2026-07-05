from __future__ import annotations

import json

import click
import polars as pl

import stock_data_engine.steps  # noqa: F401 — register steps
from stock_data_engine.config import load_config, validate_config
from stock_data_engine.derive.adj_factors import compute_adj_factors
from stock_data_engine.orchestrator.engine import JobEngine
from stock_data_engine.orchestrator.manifest import Manifest
from stock_data_engine.quality.audit import run_audit
from stock_data_engine.query.on_demand import OnDemandService
from stock_data_engine.query.views import ensure_duckdb_views
from stock_data_engine.storage import compact_dataset
from stock_data_engine.storage.layout import init_data_layout

DEFAULT_CONFIG = "configs/stockdata.example.toml"


def _cfg(config: str):
    return load_config(config)


@click.group()
@click.version_option(package_name="stock-data-engine")
def cli():
    """StockDataEngine — A-share data ingestion CLI."""


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def init(config_path: str):
    """Initialize data directories, manifest, and DuckDB views."""
    cfg = _cfg(config_path)
    init_data_layout(cfg)
    click.echo(f"Initialized StockDataEngine at {cfg.data_root}")


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
    if group_name:
        group = cfg.schedule_groups.get(group_name)
        if not group:
            raise click.ClickException(f"Unknown group: {group_name}")
        result = engine.run_job(
            f"daily:{group_name}",
            steps=group.steps,
            backfill=backfill,
        )
    else:
        result = engine.run_job("daily", backfill=backfill)
    click.echo(json.dumps({"run_id": result["run_id"], "status": result["status"]}, indent=2))


@cli.command()
@click.argument("dataset")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def backfill(dataset: str, config_path: str):
    """Backfill a dataset."""
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    result = engine.run_job("backfill", steps=[dataset], backfill=True)
    click.echo(json.dumps(result, indent=2, default=str))


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--run-id", default=None)
def compact(config_path: str, run_id: str | None):
    """Compact staging into curated."""
    cfg = _cfg(config_path)
    manifest = Manifest(cfg.manifest_path)
    if not run_id:
        latest = manifest.latest_run()
        if not latest:
            raise click.ClickException("No runs found")
        run_id = latest["run_id"]
    total = compact_dataset(cfg.staging_root, cfg.curated_root, "daily_bars", run_id)
    click.echo(f"Compacted daily_bars: {total} rows (run_id={run_id})")


@cli.command()
@click.argument("name", default="adj_factors")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def derive(name: str, config_path: str):
    """Derive computed datasets."""
    cfg = _cfg(config_path)
    if name == "adj_factors":
        rows = compute_adj_factors(cfg)
        click.echo(f"Derived {name}: {rows} rows")
    else:
        raise click.ClickException(f"Unknown derive target: {name}")


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--run-id", default=None)
def audit(config_path: str, run_id: str | None):
    """Run quality audit."""
    cfg = _cfg(config_path)
    manifest = Manifest(cfg.manifest_path)
    latest = manifest.latest_run() if not run_id else None
    rid = run_id or (latest["run_id"] if latest else "manual")
    from datetime import date

    n = run_audit(cfg, rid, date.today())
    click.echo(f"Audit complete: {n} findings written")


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def status(config_path: str):
    """Show latest run status."""
    cfg = _cfg(config_path)
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
    """Retry failed batches for a run."""
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    result = engine.run_job("retry", retry_failed_only=True, run_id=run_id)
    click.echo(json.dumps(result, indent=2, default=str))


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
                rows = sum(pl.read_parquet(f).height for f in files) if files else 0
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
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        _ = client
        click.echo("TDX connection OK")
    except ImportError:
        click.echo("mootdx not installed — install with: pip install -e '.[tdx]'")
    except Exception as exc:
        click.echo(f"TDX connection failed: {exc}", err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli()
