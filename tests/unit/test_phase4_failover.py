from datetime import date

import polars as pl

from stock_data_engine.config import Config, FailoverDatasetSpec
from stock_data_engine.quality.source_diff import diff_dataset
from stock_data_engine.storage.source_snapshots import SnapshotStore


def test_snapshot_store_roundtrip(tmp_path):
    root = tmp_path / "data"
    store = SnapshotStore(root / "meta")
    df = pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [date(2024, 6, 28)],
            "close": [1800.0],
            "open": [1790.0],
            "high": [1810.0],
            "low": [1780.0],
            "volume": [1000],
            "amount": [1_000_000.0],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    )
    path = store.write(
        "daily_bars",
        df,
        source="eastmoney",
        data_version="v1",
        run_id="run-1",
        batch_id="backup",
        trade_date=date(2024, 6, 28),
    )
    assert path is not None
    out = store.read_latest("daily_bars", source="eastmoney")
    assert out.height == 1


def test_source_diff_detects_price_drift(tmp_path):
    root = tmp_path / "data"
    curated = root / "curated" / "daily_bars" / "trade_date=2024-06-28"
    curated.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600519.SH"],
            "trade_date": [date(2024, 6, 28)],
            "close": [1800.0],
            "volume": [1000],
            "open": [1790.0],
            "high": [1810.0],
            "low": [1780.0],
            "amount": [1.0],
            "source": ["tdx_protocol"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    ).write_parquet(curated / "part-0.parquet")

    store = SnapshotStore(root / "meta")
    store.write(
        "daily_bars",
        pl.DataFrame(
            {
                "symbol": ["600519.SH"],
                "trade_date": [date(2024, 6, 28)],
                "close": [1802.0],
                "volume": [1000],
                "open": [1790.0],
                "high": [1810.0],
                "low": [1780.0],
                "amount": [1.0],
                "source": ["eastmoney"],
                "data_version": ["v1"],
                "fetched_at": ["2024-06-28T00:00:00+00:00"],
            }
        ),
        source="eastmoney",
        data_version="v1",
        run_id="run-1",
        batch_id="backup",
        trade_date=date(2024, 6, 28),
    )

    cfg = Config(
        data_root=root,
        failover_enabled=True,
        failover_datasets=[
            FailoverDatasetSpec(
                name="daily_bars",
                primary="tdx_protocol",
                backup="eastmoney",
                compare_fields=["close", "volume"],
                price_tolerance_bps=10.0,
            )
        ],
    )
    diffs = diff_dataset(cfg, cfg.failover_datasets[0], trade_date=date(2024, 6, 28))
    price_diffs = [d for d in diffs if d.get("check") == "price_drift"]
    assert len(price_diffs) == 1
    assert price_diffs[0]["bps"] > 10.0
