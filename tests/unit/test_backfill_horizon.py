"""Backfill guards for horizon-limited and chunked datasets."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ashare_lake.cli import main as cli_main
from ashare_lake.domain.datasets import DatasetSpec, get_dataset


def test_horizon_guard_refuses_a_window_the_source_cannot_serve():
    spec = get_dataset("minute_bars")
    too_old = spec.earliest_available(date.today()) - timedelta(days=1)
    with pytest.raises(cli_main.click.ClickException) as excinfo:
        cli_main._guard_history_horizon("minute_bars", too_old)
    message = str(excinfo.value)
    # The error has to say the data does not exist, not that the run failed —
    # otherwise it reads as a lake bug rather than a vendor limit.
    assert "older than the source horizon" in message
    assert str(spec.history_horizon_days) in message
    assert str(spec.earliest_available(date.today())) in message


def test_horizon_guard_allows_a_window_inside_the_horizon():
    inside = get_dataset("minute_bars").earliest_available(date.today()) + timedelta(days=1)
    cli_main._guard_history_horizon("minute_bars", inside)


def test_horizon_guard_is_a_no_op_without_a_horizon():
    # daily_bars has no vendor ceiling; a 2001 start must stay legal.
    cli_main._guard_history_horizon("daily_bars", date(2001, 1, 1))
    cli_main._guard_history_horizon("minute_bars", None)


def test_earliest_available_converts_trading_days_to_calendar_days():
    spec = DatasetSpec("x", history_horizon_days=242)
    # A year of sessions is a calendar year, not 242 calendar days.
    assert spec.earliest_available(date(2026, 8, 1)) == date(2025, 8, 1)
    assert DatasetSpec("y").earliest_available(date(2026, 8, 1)) is None


class FakeEngine:
    """Records the window each sub-run saw, via the config it is handed."""

    instances: list[FakeEngine] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.windows: list[tuple[date, date]] = []
        self.compacted: list[str] = []
        self.manifest = self
        FakeEngine.instances.append(self)

    def run_job(self, name, *, steps, backfill, finalize_run):
        self.windows.append((self.cfg._backfill_start, self.cfg._backfill_end))
        return {
            "run_id": f"run-{len(self.windows)}",
            "status": self._status(len(self.windows)),
            "rows_read": 10,
            "rows_written": 10,
        }

    def _status(self, index: int) -> str:
        return "success"

    def run_step(self, step, trade_date, run_id):
        self.compacted.append(run_id)
        return {"rows_written": 10}

    def finish_run(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _reset_engines():
    FakeEngine.instances.clear()
    yield
    FakeEngine.instances.clear()


def test_chunked_backfill_slices_the_window_and_compacts_each_slice(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "JobEngine", FakeEngine)
    cfg = type("Cfg", (), {})()

    result = cli_main._backfill_chunked(
        cfg, "minute_bars", date(2026, 7, 1), date(2026, 7, 25), chunk_days=10
    )

    engine = FakeEngine.instances[0]
    assert engine.windows == [
        (date(2026, 7, 1), date(2026, 7, 10)),
        (date(2026, 7, 11), date(2026, 7, 20)),
        (date(2026, 7, 21), date(2026, 7, 25)),
    ]
    # Every slice is drained to curated before the next one stages anything —
    # that is the whole point, since compact holds a run's staging in memory.
    assert engine.compacted == ["run-1", "run-2", "run-3"]
    assert result["status"] == "success"
    assert result["rows_written"] == 30
    assert len(result["slices"]) == 3


def test_chunked_backfill_stops_at_a_failed_slice_and_reports_where_to_resume(
    tmp_path, monkeypatch
):
    class FailingSecond(FakeEngine):
        def _status(self, index):
            return "failed" if index == 2 else "success"

    monkeypatch.setattr(cli_main, "JobEngine", FailingSecond)
    cfg = type("Cfg", (), {})()

    result = cli_main._backfill_chunked(
        cfg, "minute_bars", date(2026, 7, 1), date(2026, 7, 25), chunk_days=10
    )

    assert result["status"] == "failed"
    # The first slice stays in curated; the caller resumes from the one that broke.
    assert result["resume_from"] == date(2026, 7, 11)
    assert len(result["slices"]) == 2
    assert FailingSecond.instances[0].compacted == ["run-1"]


def test_minute_bars_declares_symbol_chunking_not_date_chunking():
    # Tip-paged: date chunks re-walk tip→start every slice. Symbol chunks keep
    # one walk per name and still bound compact memory.
    assert get_dataset("minute_bars").backfill_chunk_symbols == 200
    assert get_dataset("minute_bars").backfill_chunk_days is None
    assert get_dataset("minute_bars_5m").backfill_chunk_symbols == 200
    assert get_dataset("daily_bars").backfill_chunk_days is None
    assert get_dataset("daily_bars").backfill_chunk_symbols is None


def test_symbol_chunked_backfill_walks_full_window_per_symbol_batch(monkeypatch):
    monkeypatch.setattr(cli_main, "JobEngine", FakeEngine)
    symbols = [f"{i:06d}.SH" for i in range(250)]
    monkeypatch.setattr(
        "ashare_lake.steps.intraday.resolve_scope", lambda _cfg: symbols
    )
    cfg = type(
        "Cfg",
        (),
        {
            "minute_bars_scope": "index:000300.SH",
            "minute_bars_symbols": [],
        },
    )()

    result = cli_main._backfill_symbol_chunked(
        cfg, "minute_bars", date(2026, 3, 11), date(2026, 8, 1), chunk_symbols=200
    )

    engine = FakeEngine.instances[0]
    # Two symbol batches, each covering the full requested window once.
    assert engine.windows == [
        (date(2026, 3, 11), date(2026, 8, 1)),
        (date(2026, 3, 11), date(2026, 8, 1)),
    ]
    assert engine.compacted == ["run-1", "run-2"]
    assert result["status"] == "success"
    assert result["rows_written"] == 20
    assert [c["symbols_from"] for c in result["chunks"]] == [1, 201]
    assert [c["symbols_to"] for c in result["chunks"]] == [200, 250]
    # Scope restored so a later step in the same process sees the original.
    assert cfg.minute_bars_scope == "index:000300.SH"
    assert cfg.minute_bars_symbols == []


def test_symbol_chunked_backfill_stops_and_reports_resume_symbol(monkeypatch):
    class FailingSecond(FakeEngine):
        def _status(self, index):
            return "failed" if index == 2 else "success"

    monkeypatch.setattr(cli_main, "JobEngine", FailingSecond)
    symbols = [f"{i:06d}.SH" for i in range(250)]
    monkeypatch.setattr(
        "ashare_lake.steps.intraday.resolve_scope", lambda _cfg: symbols
    )
    cfg = type(
        "Cfg",
        (),
        {"minute_bars_scope": "all", "minute_bars_symbols": []},
    )()

    result = cli_main._backfill_symbol_chunked(
        cfg, "minute_bars", date(2026, 3, 11), date(2026, 8, 1), chunk_symbols=200
    )

    assert result["status"] == "failed"
    assert result["resume_from_symbol"] == "000200.SH"
    assert len(result["chunks"]) == 2
    assert FailingSecond.instances[0].compacted == ["run-1"]


def _backfill_argv(dataset: str, *extra: str) -> list[str]:
    return ["backfill", dataset, "--config", "cfg.toml", *extra]


def test_symbols_flag_overrides_scope_for_intraday(tmp_path, monkeypatch):
    """A one-off pull must not require editing the config first."""
    seen: dict = {}

    class Cfg:
        minute_bars_enabled = False
        minute_bars_scope = "index:000300.SH"
        minute_bars_symbols: list[str] = []
        minute_bars_frequencies = ["1m"]

    cfg = Cfg()

    def fake_backfill(config, dataset):
        seen["cfg"] = config
        return {"status": "success", "rows_written": 0}

    monkeypatch.setattr(cli_main, "_cfg", lambda _p: cfg)
    monkeypatch.setattr(cli_main, "_backfill_once", fake_backfill)

    from click.testing import CliRunner

    result = CliRunner().invoke(
        cli_main.cli,
        _backfill_argv("minute_bars_5m", "--symbols", "600519.sh, 000001.SZ"),
    )
    assert result.exit_code == 0, result.output
    assert cfg.minute_bars_scope == "watchlist"
    assert cfg.minute_bars_symbols == ["600519.SH", "000001.SZ"]
    # Enabled for this run, and the dataset's own frequency added, so a config
    # with intraday off still serves a deliberate one-off backfill.
    assert cfg.minute_bars_enabled is True
    assert "5m" in cfg.minute_bars_frequencies


def test_symbols_flag_is_rejected_for_non_intraday_datasets(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_main, "_cfg", lambda _p: object())

    from click.testing import CliRunner

    result = CliRunner().invoke(
        cli_main.cli, _backfill_argv("daily_bars", "--symbols", "600519.SH")
    )
    assert result.exit_code != 0
    assert "only applies to intraday datasets" in result.output


def test_chunked_backfill_keeps_a_warning_status_across_later_successes(monkeypatch):
    class WarnsSecond(FakeEngine):
        def _status(self, index):
            return "warning" if index == 2 else "success"

    monkeypatch.setattr(cli_main, "JobEngine", WarnsSecond)
    cfg = type("Cfg", (), {})()

    result = cli_main._backfill_chunked(
        cfg, "minute_bars", date(2026, 7, 1), date(2026, 7, 25), chunk_days=10
    )
    # A warning does not stop the sweep, and a later success must not paper
    # over the earlier warning.
    assert result["status"] == "warning"
    assert len(result["slices"]) == 3


def test_cli_backfill_takes_the_symbol_chunked_path_when_both_dates_given(monkeypatch):
    import types

    from click.testing import CliRunner

    monkeypatch.setattr(cli_main, "JobEngine", FakeEngine)
    symbols = [f"{i:06d}.SH" for i in range(250)]
    monkeypatch.setattr(
        "ashare_lake.steps.intraday.resolve_scope", lambda _cfg: symbols
    )
    cfg = types.SimpleNamespace(
        minute_bars_scope="index:000300.SH",
        minute_bars_symbols=[],
        minute_bars_enabled=True,
        minute_bars_frequencies=["1m"],
    )
    monkeypatch.setattr(cli_main, "_cfg", lambda _p: cfg)

    result = CliRunner().invoke(
        cli_main.cli,
        _backfill_argv("minute_bars", "--start", "2026-07-01", "--end", "2026-07-25"),
    )
    assert result.exit_code == 0, result.output
    engine = FakeEngine.instances[0]
    # Tip-paged path: full window once per symbol batch (200 + 50), not date slices.
    assert engine.windows == [
        (date(2026, 7, 1), date(2026, 7, 25)),
        (date(2026, 7, 1), date(2026, 7, 25)),
    ]


def test_cli_backfill_exits_nonzero_when_the_result_is_not_success(monkeypatch):
    import types

    from click.testing import CliRunner

    class AllFail(FakeEngine):
        def _status(self, index):
            return "failed"

    monkeypatch.setattr(cli_main, "JobEngine", AllFail)
    monkeypatch.setattr(
        "ashare_lake.steps.intraday.resolve_scope", lambda _cfg: ["600519.SH"]
    )
    cfg = types.SimpleNamespace(
        minute_bars_scope="watchlist",
        minute_bars_symbols=["600519.SH"],
        minute_bars_enabled=True,
        minute_bars_frequencies=["1m"],
    )
    monkeypatch.setattr(cli_main, "_cfg", lambda _p: cfg)

    result = CliRunner().invoke(
        cli_main.cli,
        _backfill_argv("minute_bars", "--start", "2026-07-01", "--end", "2026-07-10"),
    )
    assert result.exit_code == 1
