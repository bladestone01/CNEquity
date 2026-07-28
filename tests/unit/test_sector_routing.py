"""Unit tests for sector OHLC routing (no network)."""

from datetime import date

from ashare_lake.derive.sector_routing import (
    OHLC_EM,
    OHLC_TDX,
    build_sector_routing,
    is_em_exclusive,
    norm_sector_name,
)


def test_norm_sector_name_strips_suffixes():
    assert norm_sector_name("白酒Ⅱ") == "白酒"
    assert norm_sector_name("白酒概念") == "白酒"


def test_em_exclusive_tags():
    assert is_em_exclusive("2026中报预增")
    assert is_em_exclusive("Kimi概念")
    assert not is_em_exclusive("物业管理")


def test_industry_exact_routes_tdx():
    em = [{"sector_code": "BK1343", "sector_name": "物业管理", "board_type": "industry"}]
    tdx = [{"tdx_code": "881423", "name": "物业管理"}]
    df, summary = build_sector_routing(em, tdx, as_of=date(2026, 7, 14))
    row = df.row(0, named=True)
    assert row["ohlc_source"] == OHLC_TDX
    assert row["routing_tier"] == "T1"
    assert row["tdx_code"] == "881423"
    assert summary["tdx_routed"] == 1


def test_concept_exact_routes_tdx_t2():
    em = [{"sector_code": "BK0896", "sector_name": "白酒", "board_type": "concept"}]
    tdx = [{"tdx_code": "880564", "name": "白酒"}]
    df, _ = build_sector_routing(em, tdx, as_of=date(2026, 7, 14))
    assert df.row(0, named=True)["routing_tier"] == "T2"


def test_em_exclusive_forces_eastmoney():
    em = [{"sector_code": "BK1169", "sector_name": "Kimi概念", "board_type": "concept"}]
    tdx = [{"tdx_code": "880001", "name": "Kimi概念"}]
    df, _ = build_sector_routing(em, tdx, as_of=date(2026, 7, 14))
    row = df.row(0, named=True)
    assert row["ohlc_source"] == OHLC_EM
    assert row["reason"] == "em_exclusive"


def test_ambiguous_exact_stays_eastmoney():
    em = [{"sector_code": "BK0001", "sector_name": "测试", "board_type": "industry"}]
    tdx = [
        {"tdx_code": "880101", "name": "测试"},
        {"tdx_code": "880102", "name": "测试"},
    ]
    df, _ = build_sector_routing(em, tdx, as_of=date(2026, 7, 14))
    assert df.row(0, named=True)["ohlc_source"] == OHLC_EM
    assert df.row(0, named=True)["reason"] == "exact_ambiguous"


def test_fuzzy_unique_routes_t3():
    em = [{"sector_code": "BK9999", "sector_name": "东北振兴", "board_type": "concept"}]
    tdx = [{"tdx_code": "880123", "name": "东北振兴主题"}]
    df, _ = build_sector_routing(em, tdx, as_of=date(2026, 7, 14))
    row = df.row(0, named=True)
    assert row["ohlc_source"] == OHLC_TDX
    assert row["routing_tier"] == "T3"
    assert row["match_type"] == "fuzzy"


def test_fetch_tdx_indices_live_filters_88xxxx(monkeypatch):
    from ashare_lake.derive import sector_routing as sr

    class _Client:
        def __init__(self):
            self.closed = False

        def stocks(self, market):
            if market == 0:
                return [{"code": "880001", "name": "板块A"}, {"code": "000001", "name": "平安"}]
            return [{"code": "881423", "name": "物业"}, {"code": "600519", "name": "茅台"}]

        def close(self):
            self.closed = True

    client = _Client()
    # Imported inside the function from client — patch the source module.
    monkeypatch.setattr(
        "ashare_lake.adapters.tdx_protocol.client._quotes_client",
        lambda _config=None: client,
    )
    rows = sr._fetch_tdx_indices_live()
    assert {(r["tdx_code"], r["name"]) for r in rows} == {
        ("880001", "板块A"),
        ("881423", "物业"),
    }
    assert client.closed
