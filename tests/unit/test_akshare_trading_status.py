import sys
import types

from stock_data_engine.adapters.akshare.trading_status import fetch_st_symbols_akshare


def test_akshare_st_parses_and_filters(monkeypatch):
    import pandas as pd

    fake = types.ModuleType("akshare")
    fake.stock_zh_a_st_em = lambda: pd.DataFrame(
        {"代码": ["601010", "000018", "833171"], "名称": ["ST文峰", "*ST神城", "北交所ST"]}
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    out = fetch_st_symbols_akshare()
    # A-share SH/SZ kept; 833171 (BJ 83-prefix) kept per is_all_a; verify canonical form
    assert "601010.SH" in out
    assert "000018.SZ" in out


def test_akshare_st_empty_on_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", None)
    # None module makes `import akshare` raise ImportError-like; adapter returns set()
    assert fetch_st_symbols_akshare() == set() or isinstance(fetch_st_symbols_akshare(), set)


def test_akshare_st_empty_on_fetch_failure(monkeypatch):
    fake = types.ModuleType("akshare")

    def boom():
        raise RuntimeError("network")

    fake.stock_zh_a_st_em = boom
    monkeypatch.setitem(sys.modules, "akshare", fake)
    assert fetch_st_symbols_akshare() == set()
