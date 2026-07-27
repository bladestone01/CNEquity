"""Offline coverage for EastMoney capital helpers + mocked fetch_* paths."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl

from ashare_lake.adapters.eastmoney import capital as cap


def test_channel_and_margin_symbol():
    assert cap._channel("001") == "SH"
    assert cap._channel(1) == "SH"
    assert cap._channel("沪股通") == "SH"
    assert cap._channel("SH") == "SH"
    assert cap._channel("002") == "SZ"
    assert cap._channel(None) == "SZ"

    assert cap._margin_symbol({"SECUCODE": "600519.SH"}) == "600519.SH"
    assert cap._margin_symbol({"SCODE": "000001", "TRADE_MARKET": "深交所"}) == "000001.SZ"
    assert cap._margin_symbol({"SCODE": "600000", "TRADE_MARKET": "沪市"}) == "600000.SH"
    assert cap._margin_symbol({"SCODE": "430047", "TRADE_MARKET": "北交所"}) == "430047.BJ"


def test_quarter_end_dates_order_and_cutoff():
    periods = cap._quarter_end_dates(date(2016, 7, 1))
    assert periods[0] == "2016-06-30"
    assert "2016-03-31" in periods
    assert "2016-09-30" not in periods
    assert periods == sorted(periods, reverse=True)


def test_fetch_fund_flow_and_margin(monkeypatch):
    monkeypatch.setattr(
        cap,
        "fetch_clist_pages",
        lambda client, fields: [
            {"f12": "600519", "f13": 1, "f62": 1, "f66": 2, "f72": 3, "f78": 4, "f84": 5}
        ],
    )
    monkeypatch.setattr(
        cap,
        "clist_rows_to_symbols",
        lambda rows: [("600519.SH", rows[0])],
    )
    client = SimpleNamespace(close=lambda: None)
    df = cap.fetch_fund_flow(date(2025, 1, 2), client=client)
    assert df.height == 1
    assert df["main_net_inflow"][0] == 1.0

    monkeypatch.setattr(
        cap,
        "fetch_datacenter",
        lambda *a, **k: [
            {
                "SECUCODE": "000001.SZ",
                "RZYE": 10,
                "RZMRE": 1,
                "RQYE": 2,
                "RQMCL": 3,
            }
        ],
    )
    mdf = cap.fetch_margin_trading(date(2025, 1, 2), client=client)
    assert mdf.height == 1
    assert mdf["margin_balance"][0] == 10.0


def test_fetch_northbound_holdings_and_flows(monkeypatch):
    client = SimpleNamespace(
        close=lambda: None,
        get=lambda url, **k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        cap,
        "fetch_datacenter",
        lambda *a, **k: [
            {
                "SECUCODE": "600519.SH",
                "MUTUAL_TYPE": "001",
                "HOLD_SHARES": 100,
                "HOLD_MARKET_CAP": 200,
                "HOLD_SHARES_RATIO": 0.1,
            }
        ],
    )
    hdf = cap.fetch_northbound_holdings(date(2025, 3, 31), client=client)
    assert hdf.height >= 1
    assert set(hdf["channel"].to_list()) == {"SH"}

    monkeypatch.setattr(
        cap,
        "_northbound_kline_lines",
        lambda client, **k: ["2025-01-02,100,200", "bad", "not-a-date,1,2"],
    )
    fdf = cap.fetch_northbound_flows(date(2025, 1, 2), client=client)
    assert fdf.height == 2
    assert set(fdf["channel"].to_list()) == {"SH", "SZ"}

    # kamt fallback when kline empty
    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "hk2sh": {"date2": "2025-01-03", "dayNetAmtIn": 1.5},
                    "hk2sz": {"date2": "2025-01-03", "dayNetAmtIn": 2.0},
                }
            }

    monkeypatch.setattr(cap, "_northbound_kline_lines", lambda client, **k: [])
    client2 = SimpleNamespace(close=lambda: None, get=lambda url, **k: Resp())
    kamt = cap.fetch_northbound_flows(date(2025, 1, 3), client=client2)
    assert kamt.height == 2
    assert kamt.filter(pl.col("channel") == "SH")["net_buy"][0] == 1.5 * 10_000


def test_northbound_kline_retries_then_empty(monkeypatch):
    calls = {"n": 0}

    class BoomClient:
        def get(self, url, **k):
            calls["n"] += 1
            raise RuntimeError("down")

    monkeypatch.setattr(cap.time, "sleep", lambda s: None)
    assert cap._northbound_kline_lines(BoomClient(), max_retries=2) == []
    assert calls["n"] == 2


def test_fetch_dragon_tiger_and_block_trades(monkeypatch):
    client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(
        cap,
        "fetch_datacenter",
        lambda client, report, columns, filter_expr=None: (
            [
                {
                    "SECURITY_CODE": "600519",
                    "EXPLANATION": "涨幅偏离值达7%",
                    "BILLBOARD_BUY_AMT": 1,
                    "BILLBOARD_SELL_AMT": 2,
                    "BILLBOARD_NET_AMT": -1,
                }
            ]
            if "BILLBOARD" in columns
            else [
                {
                    "SECURITY_CODE": "000001",
                    "AVERAGE_PRICE": 10,
                    "VOLUME": 100,
                    "DEAL_AMT": 1000,
                    "PREMIUM_RATIO": 0.01,
                }
            ]
        ),
    )

    ddf = cap.fetch_dragon_tiger(date(2025, 1, 2), client=client)
    assert ddf.height == 1
    assert ddf["symbol"][0] == "600519.SH"

    bdf = cap.fetch_block_trades(date(2025, 1, 2), client=client)
    assert bdf.height == 1
    assert bdf["symbol"][0] == "000001.SZ"
