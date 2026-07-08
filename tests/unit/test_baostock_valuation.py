"""Offline tests for the baostock historical valuation backfill path."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from stock_data_engine.adapters.baostock.valuation import (
    fetch_valuation_history,
    to_baostock_symbol,
)
from stock_data_engine.domain.datasets import get_dataset
from stock_data_engine.domain.schemas import VALUATION_METRICS_SCHEMA


def test_to_baostock_symbol():
    assert to_baostock_symbol("600519.SH") == "sh.600519"
    assert to_baostock_symbol("000001.SZ") == "sz.000001"
    assert to_baostock_symbol("920819.BJ") == "bj.920819"


class _FakeResultSet:
    """Mimics baostock's cursor-style result set."""

    def __init__(self, rows: list[list[str]], error_code: str = "0"):
        self.error_code = error_code
        self.error_msg = "" if error_code == "0" else "boom"
        self._rows = rows
        self._i = -1

    def next(self) -> bool:
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._i]


class _FakeBaostock:
    def __init__(self, per_symbol: dict[str, list[list[str]]], login_ok: bool = True):
        self._per_symbol = per_symbol
        self._login_ok = login_ok
        self.logged_out = False

    def login(self):
        return _FakeResultSet([], error_code="0" if self._login_ok else "10001")

    def query_history_k_data_plus(self, code, fields, **kwargs):
        # code is baostock form e.g. "sh.600519"
        return _FakeResultSet(self._per_symbol.get(code, []))

    def logout(self):
        self.logged_out = True


def test_fetch_valuation_history_maps_and_nulls_market_cap():
    bs = _FakeBaostock(
        {
            "sh.600519": [
                ["2016-01-04", "sh.600519", "12.5", "3.1", "8.0"],
                ["2016-01-05", "sh.600519", "12.6", "", "8.1"],  # empty pb -> null
            ],
            "sz.000001": [
                ["2016-01-04", "sz.000001", "7.0", "0.9", "1.5"],
            ],
        }
    )
    df = fetch_valuation_history(
        ["600519.SH", "000001.SZ"], date(2016, 1, 1), date(2016, 1, 5), bs=bs
    )

    assert bs.logged_out is True
    assert df.height == 3
    # schema matches the curated contract (market cap columns present but null)
    assert set(df.columns) == set(VALUATION_METRICS_SCHEMA) - {
        "source",
        "data_version",
        "fetched_at",
    }
    assert df["total_mv"].null_count() == 3
    assert df["float_mv"].null_count() == 3
    row = df.filter(
        (pl.col("symbol") == "600519.SH") & (pl.col("trade_date") == date(2016, 1, 5))
    )
    assert row["pe_ttm"].item() == 12.6
    assert row["pb"].item() is None  # empty string parsed to null


def test_fetch_valuation_history_skips_uncovered_symbol():
    bs = _FakeBaostock({"sh.600519": [["2016-01-04", "sh.600519", "12.5", "3.1", "8.0"]]})
    # 999999.SH has no baostock coverage -> query returns empty, symbol skipped
    df = fetch_valuation_history(
        ["600519.SH", "999999.SH"], date(2016, 1, 1), date(2016, 1, 5), bs=bs
    )
    assert df.height == 1
    assert df["symbol"].unique().to_list() == ["600519.SH"]


def test_fetch_valuation_history_fails_loud_on_login_error():
    bs = _FakeBaostock({}, login_ok=False)
    with pytest.raises(RuntimeError, match="login failed"):
        fetch_valuation_history(["600519.SH"], date(2016, 1, 1), date(2016, 1, 5), bs=bs)


def test_valuation_metrics_declares_backfill_source():
    # daily semantics stay snapshot, but a historical source unlocks `sde backfill`
    spec = get_dataset("valuation_metrics")
    assert spec.fetch_semantics == "snapshot"
    assert spec.backfill_source == "baostock"
