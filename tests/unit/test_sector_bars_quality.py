"""sector_bars hybrid routing audit checks."""

from datetime import UTC, date, datetime

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.derive.sector_routing import OHLC_EM, OHLC_TDX
from stock_data_engine.quality.sector_bars import sector_bars_hybrid_findings

FETCHED = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)


def _write_bars(cfg: Config, rows: list[dict]) -> None:
    df = pl.DataFrame(rows)
    part = cfg.curated_root / "sector_bars" / f"trade_date={rows[0]['trade_date'].isoformat()}"
    part.mkdir(parents=True, exist_ok=True)
    df.write_parquet(part / "part-0.parquet")


def test_warns_when_routing_missing(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    findings = sector_bars_hybrid_findings(cfg, date(2026, 7, 14))
    assert any(f["check"] == "sector_bars_routing_missing" for f in findings)


def test_flags_tdx_routed_boards_on_em_source(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    routing = pl.DataFrame(
        [
            {
                "sector_code": "BK1631",
                "sector_name": "B",
                "board_type": "industry",
                "ohlc_source": OHLC_TDX,
                "tdx_code": "881001",
            },
        ]
    )
    path = cfg.meta_root / "sector_ohlc_routing.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    routing.write_parquet(path)

    _write_bars(
        cfg,
        [
            {
                "sector_code": "BK1631",
                "sector_name": "B",
                "board_type": "industry",
                "trade_date": date(2026, 7, 14),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "amount": 1.0,
                "change_pct": 0.0,
                "source": OHLC_EM,
                "data_version": "v1",
                "fetched_at": FETCHED,
            }
        ],
    )
    findings = sector_bars_hybrid_findings(cfg, date(2026, 7, 14))
    assert any(f["check"] == "sector_bars_tdx_routed_em_source" for f in findings)


def test_reports_source_mix(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    routing = pl.DataFrame(
        [
            {
                "sector_code": "BK1630",
                "sector_name": "A",
                "board_type": "concept",
                "ohlc_source": OHLC_EM,
                "tdx_code": None,
            },
            {
                "sector_code": "BK1631",
                "sector_name": "B",
                "board_type": "industry",
                "ohlc_source": OHLC_TDX,
                "tdx_code": "881001",
            },
        ]
    )
    path = cfg.meta_root / "sector_ohlc_routing.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    routing.write_parquet(path)

    base = {
        "trade_date": date(2026, 7, 14),
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1,
        "amount": 1.0,
        "change_pct": 0.0,
        "data_version": "v1",
        "fetched_at": FETCHED,
    }
    _write_bars(
        cfg,
        [
            {**base, "sector_code": "BK1630", "sector_name": "A", "board_type": "concept", "source": OHLC_EM},
            {**base, "sector_code": "BK1631", "sector_name": "B", "board_type": "industry", "source": OHLC_TDX},
        ],
    )
    findings = sector_bars_hybrid_findings(cfg, date(2026, 7, 14))
    mix = next(f for f in findings if f["check"] == "sector_bars_source_mix")
    assert mix["source_mix"] == {OHLC_EM: 1, OHLC_TDX: 1}
