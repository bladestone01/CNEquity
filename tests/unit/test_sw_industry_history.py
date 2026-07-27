"""Offline coverage for Shenwan industry interval helpers."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

from ashare_lake.adapters.sw import industry_history as sw


def test_exchange_and_code_to_symbol():
    assert sw.exchange_from_code("600519") == "SH"
    assert sw.exchange_from_code("000001") == "SZ"
    assert sw.exchange_from_code("920001") == "BJ"
    assert sw._code_to_symbol("600519") == "600519.SH"
    assert sw._code_to_symbol("999999") is None


def test_fetch_sw_industry_intervals(monkeypatch):
    pdf = pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "计入日期": "2021-01-01",
                "行业代码": "801780",
                "更新日期": "2021-01-02",
            },
            {
                "股票代码": "000001",
                "计入日期": "2022-06-01",
                "行业代码": "801780",
                "更新日期": "2022-06-02",
            },
            {
                "股票代码": "999999",
                "计入日期": "2021-01-01",
                "行业代码": "801780",
                "更新日期": "2021-01-02",
            },
        ]
    )

    class Resp:
        content = b"xls-bytes"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(pd, "read_excel", lambda *a, **k: pdf)
    df = sw.fetch_sw_industry_intervals(
        client=SimpleNamespace(get=lambda *a, **k: Resp(), close=lambda: None)
    )
    assert df.height == 2
    assert set(df["symbol"].to_list()) == {"600519.SH", "000001.SZ"}

    monkeypatch.setattr(pd, "read_excel", lambda *a, **k: pd.DataFrame())
    with pytest.raises(RuntimeError, match="no rows"):
        sw.fetch_sw_industry_intervals(
            client=SimpleNamespace(get=lambda *a, **k: Resp(), close=lambda: None)
        )


def test_expand_sw_industry_as_of():
    intervals = pl.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH", "000001.SZ"],
            "start_date": [date(2020, 1, 1), date(2023, 1, 1), date(2021, 1, 1)],
            "industry_code": ["A", "B", "C"],
        }
    )
    assert sw.expand_sw_industry_as_of(intervals, []).is_empty()
    out = sw.expand_sw_industry_as_of(intervals, [date(2022, 6, 1), date(2023, 6, 1)])
    # 2022: 600519→A, 000001→C; 2023: 600519→B, 000001→C
    assert out.height == 4
    latest = out.filter(
        (pl.col("symbol") == "600519.SH") & (pl.col("as_of_date") == date(2023, 6, 1))
    )
    assert latest["industry_code"][0] == "B"
