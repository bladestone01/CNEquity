from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import click
import polars as pl

import ashare_lake.steps  # noqa: F401 — register steps
from ashare_lake.config import WaveConfig, load_config, validate_config, write_user_config
from ashare_lake.derive.adj_factors import compute_adj_factors
from ashare_lake.domain.datasets import fetch_semantics, get_dataset
from ashare_lake.orchestrator.engine import JobEngine
from ashare_lake.orchestrator.manifest import Manifest
from ashare_lake.orchestrator.run_lock import RunLockError
from ashare_lake.quality.audit import run_audit
from ashare_lake.query.on_demand import OnDemandService
from ashare_lake.query.views import ensure_duckdb_views
from ashare_lake.storage.layout import init_data_layout
from ashare_lake.storage.source_snapshots import (
    DEFAULT_SNAPSHOT_RETENTION_DAYS,
    clean_source_snapshots,
)
from ashare_lake.storage.staging_cleanup import clean_staging

USER_CONFIG = "configs/ashare-lake.toml"
EXAMPLE_CONFIG = "configs/ashare-lake.example.toml"
DEFAULT_CONFIG = USER_CONFIG


def resolve_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if config_path == USER_CONFIG and not path.exists():
        raise click.ClickException(
            f"Config not found: {USER_CONFIG}. "
            "Run `asl config init` to write one from the packaged example "
            f"(or copy {EXAMPLE_CONFIG} if you have the repo checkout)."
        )
    if not path.exists():
        raise click.ClickException(f"Config not found: {path}")
    return path


def _cfg(config: str):
    return load_config(resolve_config_path(config))


@click.group()
@click.version_option(package_name="ashare-lake")
def cli():
    """ashare-lake — A-share data ingestion CLI."""


@cli.command("demo")
@click.option(
    "--symbols",
    default=",".join(
        (
            "600519.SH",
            "000001.SZ",
            "000858.SZ",
            "300750.SZ",
            "601318.SH",
        )
    ),
    show_default=True,
    help="Comma-separated symbols to fetch (kept small on purpose).",
)
@click.option(
    "--days",
    default=30,
    show_default=True,
    help="Approx. number of recent trading days of daily_bars.",
)
@click.option(
    "--data-root",
    default="data/ashare-lake-demo",
    show_default=True,
    help="Separate demo lake root (do not reuse for full-market init).",
)
@click.option(
    "--trade-date",
    "trade_date_str",
    default=None,
    help="As-of date YYYY-MM-DD (default: today / last trading day).",
)
@click.option(
    "--config-out",
    default="configs/ashare-lake.demo.toml",
    show_default=True,
    help="Where to write the tiny demo config for follow-up `asl query`.",
)
def demo_cmd(
    symbols: str,
    days: int,
    data_root: str,
    trade_date_str: str | None,
    config_out: str,
):
    """Fetch a tiny real-source lake so you can see progress and results quickly.

    Not a full-market backfill — use `asl init` for that. Requires network access
    to TDX hosts (mainland egress is more reliable overseas).
    """
    from ashare_lake.cli.demo import run_demo

    td = date.fromisoformat(trade_date_str) if trade_date_str else None
    run_demo(
        symbols=[s.strip() for s in symbols.split(",") if s.strip()],
        days=days,
        data_root=Path(data_root),
        trade_date=td,
        config_out=Path(config_out),
    )


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
                "Use `asl init --resume` or `asl retry --run-id "
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
@click.argument("action", type=click.Choice(["validate", "init"]))
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing config when action=init.",
)
@click.option(
    "--data-root",
    default=None,
    help="Set [data].root when action=init (default: resolve ./data/ashare-lake to an absolute path).",
)
def config_cmd(action: str, config_path: str, force: bool, data_root: str | None):
    """Validate or bootstrap configuration.

    ``asl config init`` writes the packaged example TOML (no repo checkout needed).
    On macOS it also forces ``orchestrator.workers = 1``.
    ``asl config validate`` checks an existing file.
    """
    if action == "init":
        out = Path(config_path)
        try:
            write_user_config(out, data_root=data_root, force=force)
        except FileExistsError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Wrote {out}")
        click.echo("data.root is absolute; edit if needed, then: asl config validate && asl init")
        return

    cfg = _cfg(config_path)
    errors = validate_config(cfg)
    if errors:
        for e in errors:
            click.echo(f"ERROR: {e}", err=True)
        raise SystemExit(1)
    click.echo("Configuration OK")


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def doctor(config_path: str, as_json: bool):
    """Check environment, optional dependencies, and config for silent breakage.

    Runs without a config (fresh install) and without network. Exits non-zero
    when something will actually lose data — notably a source that is enabled in
    config but has no package behind it, which no other command surfaces.
    """
    from ashare_lake.diagnostics.render import render_text, to_dict
    from ashare_lake.diagnostics.report import build_report

    cfg = None
    resolved: Path | None = None
    path = Path(config_path)
    if path.exists():
        try:
            cfg = load_config(path)
            resolved = path
        except Exception as exc:  # config errors must not hide the dependency report
            click.echo(f"WARN: 配置解析失败 {path}: {exc}", err=True)

    report = build_report(config=cfg, config_path=resolved)

    if as_json:
        click.echo(json.dumps(to_dict(report), indent=2, default=str))
    else:
        for line in render_text(report):
            click.echo(line)

    if not report.ok:
        raise SystemExit(1)


@cli.group()
def run():
    """Run scheduled jobs."""


@run.command("daily")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--group", "group_name", default=None, help="Schedule group: core, capital, signals")
@click.option(
    "--trade-date",
    "trade_date_str",
    default=None,
    help="As-of trade date YYYY-MM-DD (default: today). Use to catch up on weekends/holidays.",
)
@click.option("--backfill", is_flag=True)
def run_daily(
    config_path: str,
    group_name: str | None,
    trade_date_str: str | None,
    backfill: bool,
):
    """Run daily ingestion job (Wave DAG or schedule group)."""
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    td = date.fromisoformat(trade_date_str) if trade_date_str else None
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
    if result["status"] not in ("success", "skipped_non_trading_day"):
        raise SystemExit(1)


_CATCHUP_EXTRA_DEFAULT = (
    "capital",
    "signals",
    "fundamentals",
    "macro_risk",
    "research",
)


def _dataset_watermark(cfg, dataset: str):
    """Latest success date for a gate dataset (StateStore or hive max for adj)."""
    from ashare_lake.query.parquet_scan import list_hive_partition_dates
    from ashare_lake.storage.state import StateStore

    state = StateStore(cfg.meta_root)
    wm = state.get_date(dataset)
    if wm is not None:
        return wm
    if dataset == "adj_factors":
        parts = list_hive_partition_dates(cfg.derived_root / "adj_factors", "trade_date")
        return parts[-1] if parts else None
    return None


def _gate_fresh_for_catchup(cfg, trade_date: date, *, core_only: bool) -> dict[str, bool]:
    """Which gate pieces are already at/above ``trade_date``."""

    def _ok(name: str) -> bool:
        wm = _dataset_watermark(cfg, name)
        return wm is not None and wm >= trade_date

    bars_ok = _ok("daily_bars")
    adj_ok = _ok("adj_factors")
    breadth_ok = True if core_only else _ok("market_breadth")
    return {
        "daily_bars": bars_ok,
        "adj_factors": adj_ok,
        "market_breadth": breadth_ok,
        "core": bars_ok and adj_ok,
        "all": bars_ok and adj_ok and breadth_ok,
    }


@run.command("catchup")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--trade-date",
    "trade_date_str",
    default=None,
    help="Target trading day YYYY-MM-DD (default: latest trading day on/before today).",
)
@click.option(
    "--core-only",
    is_flag=True,
    help="Skip market_breadth (gate bars/adj only).",
)
@click.option(
    "--extra-group",
    "extra_groups",
    multiple=True,
    help=(
        "Also run this schedule group after the gate catchup (repeatable). "
        "Best-effort: failures are reported but do not fail the command. "
        "EM-heavy groups usually need a mainland egress."
    ),
)
@click.option(
    "--all-groups",
    is_flag=True,
    help=f"After gate catchup, best-effort run: {' '.join(_CATCHUP_EXTRA_DEFAULT)}.",
)
def run_catchup(
    config_path: str,
    trade_date_str: str | None,
    core_only: bool,
    extra_groups: tuple[str, ...],
    all_groups: bool,
):
    """Catch up core gate datasets after a missed/weekend skip.

    Runs ``daily:core`` for the target date, then ``market_breadth`` + ``compact``
    (unless ``--core-only``). Does **not** pass ``--backfill`` (full CA scan is
    fragile overseas). Optional ``--extra-group`` / ``--all-groups`` continue past
    EastMoney failures so a mainland box can refresh capital/research in one shot.
    """
    from ashare_lake.steps.common import is_trading_day, list_trading_dates

    cfg = _cfg(config_path)
    if trade_date_str:
        td = date.fromisoformat(trade_date_str)
        if not is_trading_day(cfg, td):
            raise click.ClickException(f"{td.isoformat()} is not a trading day")
    else:
        # Walk back up to ~3 weeks for long holidays.
        end = date.today()
        start = date.fromordinal(end.toordinal() - 21)
        days = list_trading_dates(cfg, start, end)
        if not days:
            raise click.ClickException("no trading day found in the last 21 calendar days")
        td = days[-1]

    extras: list[str] = []
    if all_groups:
        extras.extend(_CATCHUP_EXTRA_DEFAULT)
    extras.extend(extra_groups)
    # Preserve order, drop dupes / core (already handled).
    seen: set[str] = set()
    extras_ordered: list[str] = []
    for name in extras:
        if name == "core" or name in seen:
            continue
        seen.add(name)
        extras_ordered.append(name)

    bars_wm = _dataset_watermark(cfg, "daily_bars")
    adj_wm = _dataset_watermark(cfg, "adj_factors")
    breadth_wm = _dataset_watermark(cfg, "market_breadth")
    fresh = _gate_fresh_for_catchup(cfg, td, core_only=core_only)
    click.echo(
        json.dumps(
            {
                "trade_date": td.isoformat(),
                "daily_bars_watermark": bars_wm.isoformat() if bars_wm else None,
                "adj_factors_watermark": adj_wm.isoformat() if adj_wm else None,
                "market_breadth_watermark": breadth_wm.isoformat() if breadth_wm else None,
                "core_only": core_only,
                "extra_groups": extras_ordered,
                "already_fresh": fresh,
            },
            indent=2,
        )
    )

    engine = JobEngine(cfg)
    group = cfg.schedule_groups.get("core")
    if not group:
        raise click.ClickException("schedule group 'core' missing from config")

    results: dict[str, dict[str, str]] = {}
    try:
        if fresh["core"]:
            results["core"] = {"run_id": "", "status": "skipped_already_fresh"}
        else:
            core = engine.run_job(
                "daily:core",
                trade_date=td,
                waves=[WaveConfig(name="group:core", parallel=False, steps=group.steps)],
                backfill=False,
            )
            results["core"] = {"run_id": core["run_id"], "status": core["status"]}
            if core["status"] not in ("success", "skipped_non_trading_day"):
                click.echo(json.dumps(results, indent=2))
                raise SystemExit(1)

        if not core_only:
            if fresh["market_breadth"]:
                results["market_breadth"] = {
                    "run_id": "",
                    "status": "skipped_already_fresh",
                }
            else:
                breadth = engine.run_job(
                    "daily:market_breadth",
                    trade_date=td,
                    waves=[
                        WaveConfig(
                            name="breadth",
                            parallel=False,
                            steps=["market_breadth", "compact"],
                        )
                    ],
                    backfill=False,
                )
                results["market_breadth"] = {
                    "run_id": breadth["run_id"],
                    "status": breadth["status"],
                }
                if breadth["status"] not in ("success", "skipped_non_trading_day"):
                    click.echo(json.dumps(results, indent=2))
                    raise SystemExit(1)

        for name in extras_ordered:
            g = cfg.schedule_groups.get(name)
            if not g:
                results[name] = {"run_id": "", "status": "unknown_group"}
                continue
            out = engine.run_job(
                f"daily:{name}",
                trade_date=td,
                waves=[WaveConfig(name=f"group:{name}", parallel=False, steps=g.steps)],
                backfill=False,
            )
            results[name] = {"run_id": out["run_id"], "status": out["status"]}
    except RunLockError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(json.dumps(results, indent=2))
    # Gate path already validated; extra-group failures are advisory.
    if results["core"]["status"] not in (
        "success",
        "skipped_non_trading_day",
        "skipped_already_fresh",
    ):
        raise SystemExit(1)
    mb = results.get("market_breadth")
    if mb and mb["status"] not in (
        "success",
        "skipped_non_trading_day",
        "skipped_already_fresh",
    ):
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
    help="Range start (YYYY-MM-DD) for date-walking backfills (margin_trading, "
    "financial_statement_items period walk) and to narrow the sector_bars "
    "kline window (default: 400 days back).",
)
@click.option(
    "--end",
    "end_str",
    default=None,
    help="Range end (YYYY-MM-DD) for date-walking backfills (margin_trading, "
    "financial_statement_items period walk) and sector_bars (default: today).",
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
    # Do not finish_run until after compact — otherwise a kill between the two
    # leaves status=success with no compact batch, and `asl clean` cannot reclaim
    # staging that never reached curated (same ordering as delisted CLI).
    result = engine.run_job("backfill", steps=[dataset], backfill=True, finalize_run=False)
    run_id = result["run_id"]
    # Compact partial sweeps too. `compact` only ever drains the *current* run's
    # staging, so skipping it on a warning would strand every row the sweep did
    # fetch — while its resume checkpoint already counts those boards as done,
    # which is how a partial backfill turns into a silent hole in curated.
    if result["status"] in ("success", "warning"):
        # Through the engine, not step_compact directly: the recorded compact
        # batch is what later lets `asl clean` release this run's staging.
        compact_out = engine.run_step("compact", date.today(), run_id)
        result["compact"] = compact_out
    engine.manifest.finish_run(
        run_id,
        result["status"],
        rows_read=result.get("rows_read", 0),
        rows_written=result.get("rows_written", 0),
        error_message="one or more steps failed" if result["status"] == "failed" else None,
    )
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

    out = JobEngine(cfg).run_step("compact", date.today(), run_id)
    click.echo(
        json.dumps(
            {"run_id": run_id, "rows_written": out.get("rows_written", 0), **out},
            indent=2,
            default=str,
        )
    )


@cli.command()
@click.argument("dataset", required=False)
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--all", "do_all", is_flag=True, help="Repartition every dataset that needs it.")
@click.option("--dry-run", is_flag=True, help="Report the effect without swapping anything.")
def repartition(config_path: str, dataset: str | None, do_all: bool, dry_run: bool):
    """Rewrite a dataset's partitions at its configured granularity.

    Reads work whatever period the directories span, so this only reclaims the
    space and file opens a too-fine partitioning wastes. With no argument, lists
    the datasets whose layout does not match the registry.
    """
    from ashare_lake.storage.repartition import (
        RepartitionError,
        repartition_candidates,
        repartition_dataset,
    )

    cfg = _cfg(config_path)
    if dataset and do_all:
        raise click.ClickException("Pass a dataset or --all, not both.")

    candidates = repartition_candidates(cfg)
    if not dataset and not do_all:
        click.echo(json.dumps({"needs_repartition": candidates}, indent=2))
        return

    targets = [dataset] if dataset else candidates
    results = []
    for name in targets:
        try:
            res = repartition_dataset(cfg, name, dry_run=dry_run)
        except RepartitionError as exc:
            raise click.ClickException(str(exc)) from exc
        results.append(
            {
                "dataset": res.dataset,
                "changed": res.changed,
                "rows": res.rows,
                "files": f"{res.files_before} -> {res.files_after}",
                "partitions": f"{res.partitions_before} -> {res.partitions_after}",
                "mb": f"{res.bytes_before / 1e6:.1f} -> {res.bytes_after / 1e6:.1f}",
                "mb_saved": round(res.bytes_saved / 1e6, 1),
            }
        )
    click.echo(json.dumps({"dry_run": dry_run, "results": results}, indent=2))


@cli.command()
@click.argument("name", default="adj_factors")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Rewrite all adj_factors partitions (default: append-only since watermark).",
)
@click.option(
    "--start",
    "start_str",
    default=None,
    help="trading_status: only derive suspensions on/after this date (YYYY-MM-DD).",
)
@click.option(
    "--end",
    "end_str",
    default=None,
    help="trading_status: only derive suspensions on/before this date (YYYY-MM-DD).",
)
def derive(name: str, config_path: str, full: bool, start_str: str | None, end_str: str | None):
    """Derive computed datasets."""
    cfg = _cfg(config_path)
    if name == "adj_factors":
        result = compute_adj_factors(cfg, full=full)
        click.echo(f"Derived {name}: {result.rows} rows")
        if result.failed:
            click.echo(
                f"Warnings: {len(result.failed)} symbol×type fetch failures "
                f"({result.fail_ratio:.1%})",
                err=True,
            )
    elif name == "industry_index":
        from ashare_lake.derive.industry_index import derive_industry_index

        summary = derive_industry_index(cfg, full=full)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "trading_status":
        from ashare_lake.derive.trading_status_history import derive_suspension_history

        start = date.fromisoformat(start_str) if start_str else None
        end = date.fromisoformat(end_str) if end_str else None
        if start and end and start > end:
            raise click.ClickException("--start must be on or before --end")
        rows = derive_suspension_history(cfg, start=start, end=end)
        click.echo(f"Derived historical suspension: {rows} rows into trading_status")
    elif name == "sector_routing":
        from ashare_lake.derive.sector_routing import derive_sector_routing

        summary = derive_sector_routing(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "sector_code_map":
        from ashare_lake.derive.sector_code_map import derive_sector_code_map

        summary = derive_sector_code_map(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "valuation_orphans":
        from ashare_lake.storage.valuation_orphans import purge_valuation_orphan_symbols

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
        from ashare_lake.quality.audit import lake_health

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

    from ashare_lake.steps.common import is_trading_day

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

        from ashare_lake.domain.datasets import is_stale
        from ashare_lake.query.reader import list_datasets

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
            pl_mod.Series("freshness", [_freshness(r) for r in df.iter_rows(named=True)])
        )
        click.echo(f"last trading day: {anchor.isoformat()}")
        with pl_mod.Config(tbl_rows=-1, tbl_cols=-1, fmt_str_lengths=32):
            click.echo(df)
        stale = df.filter(pl_mod.col("freshness") == "STALE").height
        if stale:
            click.echo(f"\n{stale} dataset(s) STALE — check runs with `asl status` / `asl retry`.")
            raise SystemExit(1)
        return

    manifest = Manifest(cfg.manifest_path)
    latest = manifest.latest_run()
    if not latest:
        click.echo("No runs yet.")
        return
    summary = manifest.run_summary(latest["run_id"])
    orphaned = manifest.count_stale_running_runs(
        stale_after_seconds=cfg.batch_stale_seconds,
        locks_root=cfg.meta_root,
    )
    if orphaned:
        summary["orphaned_running_runs"] = orphaned
        summary["orphaned_note"] = (
            f"{orphaned} run(s) still status=running with no activity for "
            f">={int(cfg.batch_stale_seconds)}s — next asl run reconciles them; "
            "or `asl clean --reconcile-runs`"
        )
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
    "--snapshot-retention-days",
    default=DEFAULT_SNAPSHOT_RETENTION_DAYS,
    show_default=True,
    help="Delete meta/source_snapshots run_id dirs older than this many days "
    "(always keeps the newest per dataset/source).",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Also delete staging that is not yet cleanup-ready (incomplete batches "
        "and/or no compact). Success fetch batches are demoted to failed so "
        "`asl retry` refetches them (data is refetched, not lost, but the retry "
        "becomes a full re-run). Do not use on success-without-compact runs — "
        "run `asl compact --run-id` first."
    ),
)
@click.option(
    "--reconcile-runs",
    is_flag=True,
    help="Mark runs stuck in 'running' (crashed workers) as failed before cleanup.",
)
@click.option(
    "--reconcile-after-seconds",
    default=None,
    type=float,
    help="Only reconcile runs idle longer than this many seconds "
    "(default: [orchestrator].batch_stale_seconds).",
)
def clean(
    config_path: str,
    dry_run: bool,
    orphan_retention_days: int,
    snapshot_retention_days: int,
    force: bool,
    reconcile_runs: bool,
    reconcile_after_seconds: float | None,
):
    """Remove staging for compacted terminal runs and aged orphans.

    Ready means: run is terminal (success/warning/failed), all batches settled,
    and a successful compact batch was recorded. Incomplete or never-compacted
    staging is kept for retry unless --force is given. Also prunes aged
    ``meta/source_snapshots`` run_id dirs.
    """
    cfg = _cfg(config_path)
    reconciled: dict[str, int] | None = None
    if reconcile_runs:
        manifest = Manifest(cfg.manifest_path)
        stale_after = (
            float(reconcile_after_seconds)
            if reconcile_after_seconds is not None
            else cfg.batch_stale_seconds
        )
        reconciled = manifest.reconcile_orphaned_runs(
            stale_after_seconds=stale_after,
            locks_root=cfg.meta_root,
        )
    result = clean_staging(
        cfg,
        dry_run=dry_run,
        orphan_retention_days=orphan_retention_days,
        force=force,
    )
    snaps = clean_source_snapshots(
        cfg.meta_root,
        retention_days=snapshot_retention_days,
        dry_run=dry_run,
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
                "bytes_freed": result.bytes_freed + snaps.bytes_freed,
                "source_snapshots": {
                    "removed_run_dirs": snaps.removed_run_dirs,
                    "kept_run_dirs": snaps.kept_run_dirs,
                    "bytes_freed": snaps.bytes_freed,
                },
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
                    int(pl.scan_parquet([str(f) for f in files]).select(pl.len()).collect().item())
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
        from ashare_lake.adapters.tdx_protocol.client import _quotes_client

        cfg = _cfg(config_path)
        client = _quotes_client(cfg)
        _ = client
        click.echo("TDX connection OK")
    except ImportError:
        click.echo("TDX wire client unavailable — this is a bug, please report it")
    except Exception as exc:
        click.echo(f"TDX connection failed: {exc}", err=True)
        raise SystemExit(1) from exc


@cli.group("push2his")
def push2his_grp():
    """push2his CDN edge sticky / probe (sector_bars kline)."""


@push2his_grp.command("remember")
@click.argument("endpoint")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def push2his_remember(endpoint: str, config_path: str):
    """Save Chrome DevTools Remote Address as sticky CDN edge.

    Example: asl push2his remember 61.129.129.199:443
    """
    from ashare_lake.adapters.eastmoney.em_auth import remember_push2his_endpoint

    cfg = _cfg(config_path)
    remember_push2his_endpoint(endpoint, config=cfg)
    click.echo(f"sticky push2his edge → {endpoint.split(':')[0]}")


@push2his_grp.command("probe")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
def push2his_probe(config_path: str):
    """Discover CDN edges and probe which ones answer kline (updates sticky on hit)."""
    from ashare_lake.adapters.eastmoney.em_auth import (
        EastMoneyClient,
        _candidate_ips,
        _sticky_path,
    )

    cfg = _cfg(config_path)
    sticky = _sticky_path(cfg)
    candidates = _candidate_ips("push2his.eastmoney.com", sticky, force_discover=True)
    click.echo(f"candidates ({len(candidates)}): {', '.join(candidates)}")
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": "90.BK1152",
        "fields1": "f1",
        "fields2": "f51",
        "klt": 101,
        "fqt": 1,
        "beg": 0,
        "end": "20500101",
        "lmt": 2,
    }
    try:
        with EastMoneyClient(config=cfg) as client:
            resp = client.get(url, params=params)
        code = int(getattr(resp, "status_code", 0) or 0)
        body = getattr(resp, "text", "") or ""
        click.echo(f"probe OK status={code} bytes={len(body.encode('utf-8', 'replace'))}")
        if sticky and sticky.exists():
            click.echo(f"sticky: {sticky.read_text(encoding='utf-8').strip()}")
    except Exception as exc:
        click.echo(f"probe FAILED: {exc}", err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli()


@cli.group("delisted")
def delisted_grp():
    """Reconstruct the delisted universe (survivorship-bias repair)."""


@delisted_grp.command("discover")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--limit", default=None, type=int, help="Probe at most N codes this run.")
def delisted_discover(config_path: str, limit: int | None):
    """Sweep the issued code space for codes that used to trade.

    Resumable: a re-run continues where the last one stopped. Codes whose probe
    failed stay pending rather than being filed as never-issued.
    """
    import logging

    from ashare_lake.steps.delisted import discover_delisted

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    result = discover_delisted(_cfg(config_path), limit=limit)
    click.echo(
        json.dumps(
            {
                "probed": result.probed,
                "delisted": result.delisted,
                "never_issued": result.never_issued,
                "failed": len(result.failed),
                "remaining": result.remaining,
                "complete": result.complete,
            },
            indent=2,
        )
    )


@delisted_grp.command("status")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--since", default="2016-01-01", show_default=True, help="Lake window start.")
@click.option("--sample", default=15, show_default=True, help="Rows of detail to print.")
def delisted_status(config_path: str, since: str, sample: int):
    """Summarise the catalogue: how many, from when, and what is left to probe."""
    from collections import Counter

    from ashare_lake.steps.delisted import (
        LIVE_RECENCY_DAYS,
        classify_catalog,
        delisted_symbols_in_window,
        pending_codes,
    )

    cfg = _cfg(config_path)
    start = date.fromisoformat(since)
    catalog, live_missing = classify_catalog(cfg)
    in_window = {s: d for s, d in catalog.items() if d >= start}
    by_year = Counter(d.year for d in in_window.values())
    by_board = Counter(s.split(".")[1] for s in in_window)
    recent = sorted(in_window.items(), key=lambda kv: kv[1], reverse=True)[:sample]
    click.echo(
        json.dumps(
            {
                "delisted": len(catalog),
                "in_window": len(in_window),
                # Still quoting near the latest session: either a code the
                # instrument list is missing, or a delisting inside the recency
                # window that will reclassify on the next sweep.
                "live_or_recent": len(live_missing),
                "live_or_recent_by_exchange": dict(
                    sorted(Counter(s.split(".")[1] for s in live_missing).items())
                ),
                "live_recency_days": LIVE_RECENCY_DAYS,
                "window_start": since,
                "pending_probe": len(pending_codes(cfg)),
                "not_yet_ingested": len(delisted_symbols_in_window(cfg, start)),
                "by_year": dict(sorted(by_year.items())),
                "by_exchange": dict(sorted(by_board.items())),
                "most_recent": [{"symbol": s, "last_traded": d.isoformat()} for s, d in recent],
            },
            indent=2,
        )
    )


@delisted_grp.command("repair")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option(
    "--since",
    default=None,
    help="Only catalogued delistings on/after this date (default: all genuine).",
)
def delisted_repair(config_path: str, since: str | None):
    """Wire catalogued / orphan-bar delistings into instruments without re-fetching.

    Use this when daily_bars already holds the recovered series (e.g. from
    baostock) but instruments still has no delist_date — the gap that leaves
    ``universe=all_a`` selecting dead names. Also drops ``认购款`` stubs.
    """
    import logging

    from ashare_lake.steps.delisted import repair_delisted_instruments

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", force=True)
    cfg = _cfg(config_path)
    start = date.fromisoformat(since) if since else None
    engine = JobEngine(cfg)
    meta = {"since": since} if since else {}
    run_id = engine.manifest.start_run("delisted_repair", meta)
    result = repair_delisted_instruments(cfg, run_id, start=start)
    compact_out = engine.run_step("compact", date.today(), run_id)
    # Compact can re-introduce nothing for placeholders; purge once more after
    # the merge in case an older curated copy still carried them.
    from ashare_lake.steps.delisted import purge_subscription_placeholders

    result["purged_placeholders_after_compact"] = purge_subscription_placeholders(cfg)
    engine.manifest.finish_run(run_id, "success", rows_written=result.get("rows_written", 0))
    ensure_duckdb_views(cfg)
    click.echo(
        json.dumps({"run_id": run_id, **result, "compact": compact_out}, indent=2, default=str)
    )


@delisted_grp.command("backfill")
@click.option("--config", "config_path", default=DEFAULT_CONFIG, show_default=True)
@click.option("--since", default="2016-01-01", show_default=True, help="Lake window start.")
def delisted_backfill(config_path: str, since: str):
    """Fetch price history for catalogued delistings and compact it into the lake."""
    import logging

    from ashare_lake.steps.delisted import backfill_delisted_bars

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = _cfg(config_path)
    engine = JobEngine(cfg)
    run_id = engine.manifest.start_run("delisted_backfill", {"since": since})
    result = backfill_delisted_bars(cfg, run_id, date.fromisoformat(since))
    compact_out = engine.run_step("compact", date.today(), run_id)
    engine.manifest.finish_run(run_id, "success", rows_written=result.get("rows_written", 0))
    click.echo(
        json.dumps({"run_id": run_id, **result, "compact": compact_out}, indent=2, default=str)
    )
