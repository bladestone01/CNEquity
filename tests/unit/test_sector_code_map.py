"""BK ↔ BOARD_CODE identity map."""

from datetime import date

import polars as pl

from stock_data_engine.derive.sector_code_map import (
    board_code_to_bk,
    bk_to_board_code,
    build_sector_code_map,
)


def test_bk_board_code_roundtrip():
    assert board_code_to_bk("437") == "BK0437"
    assert board_code_to_bk("1628") == "BK1628"
    assert bk_to_board_code("BK0437") == "437"
    assert bk_to_board_code("BK1628") == "1628"
    assert bk_to_board_code("bad") is None


def test_build_identity_map():
    bars = pl.DataFrame(
        [
            {"sector_code": "BK0437", "sector_name": "煤炭", "board_type": "industry", "trade_date": date(2026, 7, 14)},
            {"sector_code": "BK0896", "sector_name": "白酒", "board_type": "concept", "trade_date": date(2026, 7, 14)},
            {"sector_code": "BK0636", "sector_name": "B股", "board_type": "concept", "trade_date": date(2026, 7, 14)},
        ]
    )
    concept = pl.DataFrame([{"board_code": "896", "board_name": "白酒"}])
    industry = pl.DataFrame([{"board_code": "437", "board_name": "煤炭"}])
    df, summary = build_sector_code_map(bars, concept, industry, as_of=date(2026, 7, 14))
    assert df.height == 3
    hit = df.filter(pl.col("has_members"))
    assert hit.height == 2
    assert set(hit["match_type"].to_list()) == {"identity"}
    orphan = df.filter(~pl.col("has_members"))
    assert orphan["sector_code"][0] == "BK0636"
    assert orphan["board_code"][0] == "636"
    assert summary["has_members"] == 2


def test_identity_name_mismatch_flagged():
    bars = pl.DataFrame(
        [
            {
                "sector_code": "BK0896",
                "sector_name": "白酒概念",
                "board_type": "concept",
                "trade_date": date(2026, 7, 14),
            }
        ]
    )
    # Same id, totally different name → mismatch flag (norm still matches 白酒)
    concept = pl.DataFrame([{"board_code": "896", "board_name": "白酒"}])
    df, _ = build_sector_code_map(bars, concept, pl.DataFrame(), as_of=date(2026, 7, 14))
    assert df["match_type"][0] == "identity"  # 白酒概念 vs 白酒 normalize equal

    concept2 = pl.DataFrame([{"board_code": "896", "board_name": "光伏"}])
    df2, _ = build_sector_code_map(bars, concept2, pl.DataFrame(), as_of=date(2026, 7, 14))
    assert df2["match_type"][0] == "identity_name_mismatch"
