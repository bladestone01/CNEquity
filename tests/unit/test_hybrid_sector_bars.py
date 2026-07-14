"""Hybrid sector_bars daily fetch."""

from datetime import date
from unittest.mock import patch

import polars as pl

from stock_data_engine.adapters.hybrid.sector_bars import fetch_hybrid_sector_bars
from stock_data_engine.config import Config
from stock_data_engine.derive.sector_routing import OHLC_EM, OHLC_TDX


def _em_row(sector_code: str, close: float = 100.0) -> dict:
    return {
        "sector_code": sector_code,
        "sector_name": "X",
        "board_type": "concept",
        "trade_date": date(2026, 7, 14),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
        "amount": 1.0,
        "change_pct": 0.0,
    }


def test_hybrid_daily_replaces_tdx_routed_boards(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    routing = pl.DataFrame(
        [
            {
                "sector_code": "BK1630",
                "sector_name": "EM only",
                "board_type": "concept",
                "ohlc_source": OHLC_EM,
                "tdx_code": None,
            },
            {
                "sector_code": "BK1631",
                "sector_name": "TDX routed",
                "board_type": "industry",
                "ohlc_source": OHLC_TDX,
                "tdx_code": "881001",
            },
        ]
    )
    path = cfg.meta_root / "sector_ohlc_routing.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    routing.write_parquet(path)

    em_df = pl.DataFrame([_em_row("BK1630", 100.0), _em_row("BK1631", 200.0)])
    tdx_snap = {**_em_row("BK1631", 150.0)}

    with patch(
        "stock_data_engine.adapters.hybrid.sector_bars.fetch_sector_bars",
        return_value=em_df,
    ), patch(
        "stock_data_engine.adapters.hybrid.sector_bars.fetch_sector_index_snapshot",
        return_value=tdx_snap,
    ):
        df = fetch_hybrid_sector_bars(date(2026, 7, 14), config=cfg)

    assert df.height == 2
    em_only = df.filter(pl.col("sector_code") == "BK1630")
    tdx_row = df.filter(pl.col("sector_code") == "BK1631")
    assert em_only["source"][0] == OHLC_EM
    assert em_only["close"][0] == 100.0
    assert tdx_row["source"][0] == OHLC_TDX
    assert tdx_row["close"][0] == 150.0


def test_hybrid_daily_falls_back_to_em_when_no_routing(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    em_df = pl.DataFrame([_em_row("BK1630")])

    with patch(
        "stock_data_engine.adapters.hybrid.sector_bars.fetch_sector_bars",
        return_value=em_df,
    ):
        df = fetch_hybrid_sector_bars(date(2026, 7, 14), config=cfg)

    assert df["source"][0] == OHLC_EM


def test_hybrid_daily_keeps_em_when_tdx_snapshot_missing(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    routing = pl.DataFrame(
        [
            {
                "sector_code": "BK1631",
                "sector_name": "TDX routed",
                "board_type": "industry",
                "ohlc_source": OHLC_TDX,
                "tdx_code": "881001",
            },
        ]
    )
    path = cfg.meta_root / "sector_ohlc_routing.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    routing.write_parquet(path)

    em_df = pl.DataFrame([_em_row("BK1631", 200.0)])

    with patch(
        "stock_data_engine.adapters.hybrid.sector_bars.fetch_sector_bars",
        return_value=em_df,
    ), patch(
        "stock_data_engine.adapters.hybrid.sector_bars.fetch_sector_index_snapshot",
        return_value=None,
    ):
        df = fetch_hybrid_sector_bars(date(2026, 7, 14), config=cfg)

    assert df["close"][0] == 200.0
    assert df["source"][0] == OHLC_EM
