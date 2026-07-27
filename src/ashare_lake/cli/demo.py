"""One-command mini demo: real TDX fetch for a handful of liquid names.

Designed for first-run / star-seeker UX — not a substitute for ``asl init``.
Writes into a separate data root so a later full-market init is not poisoned
by a 5-symbol instruments file.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import click
import polars as pl

from ashare_lake.config import Config, WaveConfig
from ashare_lake.domain.schemas import validate_dataframe, with_provenance
from ashare_lake.orchestrator.engine import JobEngine
from ashare_lake.storage.atomic import write_parquet_atomic
from ashare_lake.storage.layout import init_data_layout

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = (
    "600519.SH",  # 贵州茅台
    "000001.SZ",  # 平安银行
    "000858.SZ",  # 五粮液
    "300750.SZ",  # 宁德时代
    "601318.SH",  # 中国平安
)
DEFAULT_DAYS = 30
DEFAULT_DATA_ROOT = Path("data/ashare-lake-demo")


def _banner(step: str, title: str) -> None:
    click.echo(f"\n=== [{step}] {title} ===", err=False)
    sys.stdout.flush()


def _write_demo_toml(path: Path, data_root: Path) -> None:
    """Persist a tiny config so follow-up ``asl query --config …`` works."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Auto-written by `asl demo`. Safe to delete with the demo data_root.
[data]
root = "{data_root.resolve()}"

[orchestrator]
workers = 1
batch_size = 50

[tdx_protocol]
enabled = true
allow_mock = false
min_interval_ms = 100
servers = "auto"
""",
        encoding="utf-8",
    )


def _demo_config(data_root: Path, config_path: Path | None = None) -> Config:
    """Minimal real-source config (workers=1, no mock, TDX only)."""
    return Config(
        data_root=data_root.resolve(),
        workers=1,
        batch_size=50,
        tdx_enabled=True,
        tdx_allow_mock=False,
        tdx_min_interval_ms=100,
        tdx_servers="auto",
        # Prefer known-good CN hosts (same pool as the example config).
        tdx_host_pool=[
            "120.76.1.198:7709",
            "123.125.108.101:7709",
            "114.141.177.118:7709",
            "27.151.2.113:7709",
            "182.118.8.9:7709",
        ],
        sources={
            "eastmoney": False,
            "sina": False,
            "cninfo": False,
            "baostock": False,
            "akshare": False,
        },
        failover_enabled=False,
        daily_waves=[WaveConfig(name="demo", parallel=False, steps=["daily_bars", "compact"])],
        config_path=config_path,
    )


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # Keep httpx noise down; TDX/session logs stay visible.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _probe_tdx(cfg: Config) -> None:
    from ashare_lake.adapters.tdx_protocol.client import _quotes_client

    t0 = time.perf_counter()
    click.echo("Probing TDX hosts (first successful server wins)…")
    sys.stdout.flush()
    client = _quotes_client(cfg)
    _ = client
    click.echo(f"TDX connection OK ({time.perf_counter() - t0:.1f}s)")


def _write_demo_instruments(cfg: Config, symbols: list[str]) -> list[str]:
    from ashare_lake.adapters.tdx_protocol.client import fetch_instruments, normalize_with_source

    click.echo(f"Fetching full instrument list, then keeping {len(symbols)} demo symbols…")
    sys.stdout.flush()
    raw = fetch_instruments(
        rate_limit=cfg.tdx_rate_limit_spec(),
        allow_mock=False,
        config=cfg,
    )
    raw = normalize_with_source(raw)
    wanted = set(symbols)
    kept = raw.filter(pl.col("symbol").is_in(list(wanted)))
    found = set(kept["symbol"].to_list())
    missing = [s for s in symbols if s not in found]
    if missing:
        click.echo(
            f"Warning: not in TDX list (skipped): {', '.join(missing)}",
            err=True,
        )
    if kept.is_empty():
        raise click.ClickException(
            "None of the demo symbols were returned by TDX. "
            "Check connectivity with `asl servers test` or pass --symbols."
        )
    df = validate_dataframe(
        with_provenance(kept, source="tdx_protocol", data_version="v1"),
        "instruments",
    )
    out = cfg.curated_root / "instruments" / "part-merged.parquet"
    write_parquet_atomic(out, df, compression="zstd")
    click.echo(f"Wrote {df.height} instruments → {out}")
    return df["symbol"].to_list()


def _last_trading_day(cfg: Config, as_of: date) -> date:
    from ashare_lake.steps.common import list_trading_dates

    window = list_trading_dates(cfg, as_of - timedelta(days=21), as_of)
    return window[-1] if window else as_of


def _start_for_days(cfg: Config, end: date, days: int) -> date:
    from ashare_lake.steps.common import list_trading_dates

    # Pull a padded calendar window, then take the last `days` sessions.
    probe_start = end - timedelta(days=max(days * 3, 60))
    sessions = list_trading_dates(cfg, probe_start, end)
    if not sessions:
        return end - timedelta(days=days)
    if len(sessions) <= days:
        return sessions[0]
    return sessions[-days]


def _sample_query(cfg: Config, symbol: str) -> pl.DataFrame:
    from ashare_lake.query.reader import load

    return (
        load(
            "daily_bars",
            start=None,
            end=None,
            symbols=[symbol],
            config=cfg,
        )
        .sort("trade_date", descending=True)
        .head(8)
    )


def run_demo(
    *,
    symbols: list[str],
    days: int,
    data_root: Path,
    trade_date: date | None = None,
    config_out: Path | None = None,
) -> dict:
    """Run the mini real-source demo. Returns a small summary dict."""
    _configure_logging()
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if not symbols:
        raise click.ClickException("--symbols must list at least one symbol")
    if days < 1:
        raise click.ClickException("--days must be >= 1")

    config_out = config_out or Path("configs/ashare-lake.demo.toml")
    _banner("1/6", f"Prepare demo lake at {data_root}")
    _write_demo_toml(config_out, data_root)
    cfg = _demo_config(data_root, config_path=config_out.resolve())
    init_data_layout(cfg)
    click.echo(f"data_root = {cfg.data_root}")
    click.echo(f"config    = {config_out}")
    click.echo("Note: this is a SEPARATE lake from a full `asl init` — safe to wipe.")

    _banner("2/6", "Probe TDX")
    try:
        _probe_tdx(cfg)
    except Exception as exc:
        raise click.ClickException(
            f"TDX unreachable: {exc}\n"
            "Tips: try from a mainland network / VPN egress, or check "
            "`[tdx_protocol.hosts]` in the example config."
        ) from exc

    _banner("3/6", "Instruments (demo universe)")
    kept = _write_demo_instruments(cfg, symbols)

    _banner("4/6", "Trading calendar")
    engine = JobEngine(cfg)
    as_of = trade_date or date.today()
    # Seed calendar covers a wide range; backfill window is cheap (CSV/seed).
    cfg._backfill = True
    cfg._backfill_start = date(2020, 1, 1)
    cfg._backfill_end = as_of
    cal = engine.run_job(
        "demo:calendar",
        trade_date=as_of,
        steps=["trading_calendar"],
        backfill=True,
    )
    if cal.get("status") not in ("success", "warning"):
        raise click.ClickException(f"trading_calendar failed: {cal}")
    end = _last_trading_day(cfg, as_of)
    start = _start_for_days(cfg, end, days)
    click.echo(f"Demo window: {start.isoformat()} → {end.isoformat()} ({days} trading days target)")

    _banner("5/6", f"daily_bars for {len(kept)} symbols")
    cfg._backfill = True
    cfg._backfill_start = start
    cfg._backfill_end = end
    bars = engine.run_job(
        "demo:bars",
        trade_date=end,
        waves=[WaveConfig(name="bars", parallel=False, steps=["daily_bars", "compact"])],
        backfill=True,
    )
    if bars.get("status") not in ("success", "warning"):
        raise click.ClickException(
            f"daily_bars failed: {bars}\nRe-run `asl demo` after fixing TDX connectivity."
        )
    click.echo(
        f"Bars run {bars.get('run_id')}: status={bars.get('status')} "
        f"rows_written≈{bars.get('rows_written', '?')}"
    )

    _banner("6/6", "Sample result")
    sample_symbol = kept[0]
    try:
        sample = _sample_query(cfg, sample_symbol)
    except Exception as exc:
        raise click.ClickException(f"query failed after demo write: {exc}") from exc
    if sample.is_empty():
        raise click.ClickException(
            f"No daily_bars rows for {sample_symbol}. TDX may have returned an empty window."
        )
    with pl.Config(tbl_rows=10, tbl_cols=-1, fmt_str_lengths=24):
        click.echo(f"\n{sample_symbol} — latest rows:\n")
        click.echo(
            sample.select(
                [
                    c
                    for c in (
                        "symbol",
                        "trade_date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "source",
                    )
                    if c in sample.columns
                ]
            )
        )

    click.echo(
        f"""
Demo lake ready under: {cfg.data_root}
Config written to:     {config_out}

Next:
  asl query --config {config_out} --sql "
    SELECT symbol, trade_date, close, volume, source
    FROM daily_bars
    WHERE symbol = '{sample_symbol}'
    ORDER BY trade_date DESC
    LIMIT 10
  "

Python:
  from ashare_lake.query import load
  bars = load("daily_bars", symbols=["{sample_symbol}"], data_root="{cfg.data_root}")

Full-market backfill (hours/days) is separate: `asl config init` then `asl init`.
Do not reuse this demo data_root for production.
"""
    )
    return {
        "data_root": str(cfg.data_root),
        "config": str(config_out),
        "symbols": kept,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sample_symbol": sample_symbol,
        "sample_rows": sample.height,
        "bars_run_id": bars.get("run_id"),
    }
