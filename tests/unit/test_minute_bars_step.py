from datetime import date, datetime, timedelta

import polars as pl
import pytest

from ashare_lake.config import Config
from ashare_lake.domain.datasets import get_dataset
from ashare_lake.domain.schemas import with_provenance
from ashare_lake.steps import intraday
from ashare_lake.steps.intraday import (
    MinuteBarsScopeError,
    capture_intraday_bars,
    resolve_scope,
)
from ashare_lake.storage import StagingWriter


@pytest.fixture
def cfg(tmp_path):
    config = Config(data_root=tmp_path / "lake")
    for sub in ("staging", "curated", "meta"):
        (config.data_root / sub).mkdir(parents=True, exist_ok=True)
    return config


def _write_constituents(config: Config, index_symbol: str, symbols: list[str], as_of: date):
    root = config.curated_root / "index_constituents" / f"as_of_date={as_of:%Y-%m}"
    root.mkdir(parents=True, exist_ok=True)
    df = with_provenance(
        pl.DataFrame(
            {
                "index_symbol": [index_symbol] * len(symbols),
                "symbol": symbols,
                "as_of_date": [as_of] * len(symbols),
                "weight": [1.0] * len(symbols),
            }
        ),
        source="test",
        data_version="v1",
    )
    df.write_parquet(root / f"part-{index_symbol}-{as_of:%Y%m%d}.parquet")


def test_scope_index_uses_latest_as_of(cfg):
    _write_constituents(cfg, "000300.SH", ["600519.SH", "000001.SZ"], date(2026, 6, 30))
    _write_constituents(cfg, "000300.SH", ["600519.SH", "300750.SZ"], date(2026, 7, 31))
    # A rebalance replaces the roster; carrying both as_of dates forward would
    # silently capture names the index no longer holds.
    assert resolve_scope(cfg) == ["300750.SZ", "600519.SH"]


def test_scope_index_ignores_other_indices(cfg):
    _write_constituents(cfg, "000300.SH", ["600519.SH"], date(2026, 7, 31))
    _write_constituents(cfg, "000905.SH", ["603005.SH"], date(2026, 7, 31))
    assert resolve_scope(cfg) == ["600519.SH"]


def test_scope_index_without_constituents_names_the_fix(cfg):
    with pytest.raises(MinuteBarsScopeError, match="index_constituents"):
        resolve_scope(cfg)


def test_scope_watchlist(cfg):
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH", " 000001.SZ "]
    assert resolve_scope(cfg) == ["600519.SH", "000001.SZ"]


def test_scope_watchlist_requires_symbols(cfg):
    cfg.minute_bars_scope = "watchlist"
    with pytest.raises(MinuteBarsScopeError, match="symbols is empty"):
        resolve_scope(cfg)


def test_scope_unknown_value(cfg):
    cfg.minute_bars_scope = "sp500"
    with pytest.raises(MinuteBarsScopeError, match="unknown"):
        resolve_scope(cfg)


def test_scope_all_drops_beijing(cfg, monkeypatch):
    # TDX serves no BJ intraday route, so including them would make every run
    # report hundreds of failures that can never succeed.
    monkeypatch.setattr(
        intraday, "load_symbols", lambda _c: ["600519.SH", "920819.BJ", "000001.SZ"]
    )
    cfg.minute_bars_scope = "all"
    assert resolve_scope(cfg) == ["600519.SH", "000001.SZ"]


def test_step_is_a_no_op_when_disabled(cfg):
    result = capture_intraday_bars(
        cfg, date(2026, 7, 31), "run-1", dataset="minute_bars", frequency="1m"
    )
    assert result["rows_written"] == 0
    assert "disabled" in result["note"]


def test_step_skips_a_frequency_the_config_did_not_ask_for(cfg):
    # Both intraday steps are registered and both are on the intraday group, so
    # the one whose frequency is not configured has to no-op rather than fetch.
    cfg.minute_bars_enabled = True
    cfg.minute_bars_frequencies = ["1m"]
    result = capture_intraday_bars(
        cfg, date(2026, 7, 31), "run-1", dataset="minute_bars_5m", frequency="5m"
    )
    assert result["rows_written"] == 0
    assert "not in [minute_bars].frequencies" in result["note"]


def test_5m_step_runs_when_configured(cfg, monkeypatch):
    cfg.minute_bars_enabled = True
    cfg.minute_bars_frequencies = ["5m"]
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH"]
    monkeypatch.setattr(intraday, "fetch_minute_bars", _fake_fetch())

    result = capture_intraday_bars(
        cfg, date(2026, 7, 31), "run-1", dataset="minute_bars_5m", frequency="5m"
    )

    assert result["rows_written"] == 2
    staged = pl.read_parquet(
        StagingWriter(cfg.staging_root).list_run_files("minute_bars_5m", "run-1")
    )
    # Rows land in the 5m dataset, stamped 5m — not mixed into minute_bars.
    assert staged["frequency"].unique().to_list() == ["5m"]
    assert not StagingWriter(cfg.staging_root).list_run_files("minute_bars", "run-1")


def test_each_intraday_dataset_has_its_own_registered_step():
    import ashare_lake.steps  # noqa: F401 — register steps
    from ashare_lake.domain.datasets import intraday_datasets
    from ashare_lake.orchestrator.registry import STEP_REGISTRY

    for frequency, dataset in intraday_datasets().items():
        assert dataset in STEP_REGISTRY, f"{frequency} has no step"
        assert STEP_REGISTRY[dataset].group == "intraday"


def _fake_fetch(rows_per_symbol: int = 2, failed: list[str] | None = None):
    def fetch(symbols, start, end, *, frequency, **kwargs):
        rows = []
        for sym in symbols:
            for i in range(rows_per_symbol):
                stamp = datetime(end.year, end.month, end.day, 9, 31 + i)
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": end,
                        "bar_time": stamp,
                        "frequency": frequency,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 100,
                        "amount": 1000.0,
                    }
                )
        return pl.DataFrame(rows), list(failed or [])

    return fetch


def test_step_stages_rows_for_the_configured_scope(cfg, monkeypatch):
    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH", "000001.SZ"]
    monkeypatch.setattr(intraday, "fetch_minute_bars", _fake_fetch())

    result = capture_intraday_bars(
        cfg, date(2026, 7, 31), "run-1", dataset="minute_bars", frequency="1m"
    )

    assert result["rows_written"] == 4
    assert result["symbols"] == 2
    files = StagingWriter(cfg.staging_root).list_run_files("minute_bars", "run-1")
    staged = pl.read_parquet(files)
    assert staged.height == 4
    # validate_dataframe runs on write, so the staged frame is already the
    # curated contract — provenance included.
    assert {"source", "data_version", "fetched_at"} <= set(staged.columns)
    assert staged["frequency"].unique().to_list() == ["1m"]


def test_step_reports_failed_symbols_as_a_finding(cfg, monkeypatch):
    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH", "000001.SZ"]
    monkeypatch.setattr(intraday, "fetch_minute_bars", _fake_fetch(failed=["000001.SZ"]))

    result = capture_intraday_bars(
        cfg, date(2026, 7, 31), "run-1", dataset="minute_bars", frequency="1m"
    )

    findings = result["context_updates"]["audit_findings"]
    assert findings[0]["check"] == "minute_bars_symbol_fetch"
    assert "000001.SZ" in findings[0]["message"]


def test_step_fails_when_nothing_at_all_came_back(cfg, monkeypatch):
    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH"]
    monkeypatch.setattr(
        intraday, "fetch_minute_bars", lambda *a, **k: (pl.DataFrame({"symbol": []}), [])
    )
    with pytest.raises(RuntimeError, match="no rows for any"):
        capture_intraday_bars(
            cfg, date(2026, 7, 31), "run-1", dataset="minute_bars", frequency="1m"
        )


def test_backfill_window_is_clamped_to_the_source_horizon(cfg, monkeypatch):
    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH"]
    cfg._backfill = True
    cfg._backfill_start = date(2016, 1, 1)
    cfg._backfill_end = date(2026, 7, 31)

    seen: dict = {}

    def fetch(symbols, start, end, **kwargs):
        seen["start"] = start
        return _fake_fetch()(symbols, start, end, **kwargs)

    monkeypatch.setattr(intraday, "fetch_minute_bars", fetch)
    capture_intraday_bars(cfg, date(2026, 7, 31), "run-1", dataset="minute_bars", frequency="1m")

    # 95 trading days back, not 2016: the source has nothing older, so sweeping
    # a decade would spend hours confirming it.
    horizon = get_dataset("minute_bars").earliest_available(date(2026, 7, 31))
    assert seen["start"] == horizon
    assert date(2026, 7, 31) - seen["start"] < timedelta(days=200)


def test_step_reports_symbols_that_returned_no_rows(cfg, monkeypatch):
    """A suspended name legitimately has no bars — but the count must be visible.

    Without it, "asked for 5,400, wrote 5,100" is indistinguishable from a
    silent fetch hole. Measured on a real 60-symbol sweep: 4 symbols returned
    nothing, all four suspended that day (volume=0 in daily_bars).
    """
    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH", "000001.SZ", "600485.SH"]

    def fetch(symbols, start, end, *, frequency, **kwargs):
        live = [s for s in symbols if s != "600485.SH"]
        return _fake_fetch()(live, start, end, frequency=frequency, **kwargs)

    monkeypatch.setattr(intraday, "fetch_minute_bars", fetch)
    result = capture_intraday_bars(
        cfg, date(2026, 7, 31), "run-1", dataset="minute_bars", frequency="1m"
    )

    assert result["symbols"] == 3
    assert result["symbols_with_rows"] == 2
    assert result["failed_symbols"] == 0
    # Silence is not an error: a suspended name must not fail the step.
    assert "context_updates" not in result


def test_scope_index_symbol_not_present_in_constituents_data(cfg):
    # index_constituents has data, just not for the requested index — distinct
    # from the "dataset is empty" case, which points at a different fix.
    _write_constituents(cfg, "000300.SH", ["600519.SH"], date(2026, 7, 31))
    cfg.minute_bars_scope = "index:000905.SH"
    with pytest.raises(MinuteBarsScopeError, match="holds no rows for '000905.SH'"):
        resolve_scope(cfg)


def test_empty_window_is_reported_not_fetched(cfg, monkeypatch):
    # A misconfigured window (start after end) must not reach the fetcher at
    # all — there is nothing honest it could return for it.
    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH"]
    cfg._backfill = True
    cfg._backfill_start = date(2026, 8, 5)
    cfg._backfill_end = date(2026, 8, 1)

    called = []
    monkeypatch.setattr(intraday, "fetch_minute_bars", lambda *a, **k: called.append(1))
    result = capture_intraday_bars(
        cfg, date(2026, 8, 1), "run-1", dataset="minute_bars", frequency="1m"
    )
    assert result["rows_written"] == 0
    assert "empty window" in result["note"]
    assert called == []


def test_registered_step_delegates_with_its_own_dataset_and_frequency(cfg, monkeypatch):
    """The generated per-dataset step closure, not the helper it wraps.

    Guards the late-binding default-argument pattern in
    ``_register_intraday_steps``: without ``_dataset``/``_frequency`` bound as
    defaults, every generated step would silently call through with whichever
    dataset the loop last saw.
    """
    import ashare_lake.steps  # noqa: F401 — register steps
    from ashare_lake.orchestrator.registry import STEP_REGISTRY

    cfg.minute_bars_enabled = True
    cfg.minute_bars_scope = "watchlist"
    cfg.minute_bars_symbols = ["600519.SH"]
    cfg.minute_bars_frequencies = ["5m"]
    monkeypatch.setattr(intraday, "fetch_minute_bars", _fake_fetch())

    step_fn = STEP_REGISTRY["minute_bars_5m"].fn
    result = step_fn(cfg, date(2026, 7, 31), "run-1", {})

    assert result["rows_written"] > 0
    staged = pl.read_parquet(
        StagingWriter(cfg.staging_root).list_run_files("minute_bars_5m", "run-1")
    )
    assert staged["frequency"].unique().to_list() == ["5m"]


def test_approx_trading_days_uses_the_real_calendar_when_available(cfg):
    from ashare_lake.steps.intraday import _approx_trading_days

    start, end = date(2026, 7, 27), date(2026, 7, 31)
    rows = [
        {
            "trade_date": d,
            "is_trading": d.weekday() < 5,
            "source": "seed",
            "data_version": "v1",
            "fetched_at": "2026-07-31T00:00:00+00:00",
        }
        for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
    ]
    df = with_provenance(
        pl.DataFrame(rows).drop("source", "data_version", "fetched_at"),
        source="seed",
        data_version="v1",
    )
    out = cfg.curated_root / "trading_calendar" / "trade_date=2026"
    out.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out / "part-0.parquet")

    # 2026-07-27..31 is Mon-Fri: 5 real trading days. The 5/7 fallback would
    # give 4 for this same window, so a wrong (fallback) answer here is
    # distinguishable from the real-calendar one being exercised.
    assert _approx_trading_days(cfg, start, end) == 5
