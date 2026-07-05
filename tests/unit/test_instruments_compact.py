from datetime import UTC, date, datetime

import polars as pl

import stock_data_engine.steps  # noqa: F401
from stock_data_engine.config import Config
from stock_data_engine.query.universe import tradable_symbols_on_date
from stock_data_engine.steps.finalize import step_compact
from stock_data_engine.storage import StagingWriter
from stock_data_engine.storage.instruments import compact_instruments


def _prov(source: str = "tdx_protocol") -> dict:
    return {
        "source": source,
        "data_version": "v1",
        "fetched_at": datetime(2024, 6, 28, tzinfo=UTC),
    }


def _instrument(symbol: str, *, list_date: date | None = None, delist_date: date | None = None) -> dict:
    exchange = symbol.split(".")[1]
    return {
        "symbol": symbol,
        "name": symbol,
        "exchange": exchange,
        "asset_type": "stock",
        "list_date": list_date,
        "delist_date": delist_date,
        "prev_symbol": None,
        **_prov(),
    }


def test_compact_instruments_preserves_missing_symbols_and_marks_delist(tmp_path):
    root = tmp_path / "data"
    cfg = Config(data_root=root)
    run_id = "run-inst"
    trade_date = date(2024, 6, 28)

    curated_path = cfg.curated_root / "instruments" / "part-merged.parquet"
    curated_path.parent.mkdir(parents=True)
    pl.DataFrame(
        [
            _instrument("600519.SH", list_date=date(2001, 8, 27)),
            _instrument("000001.SZ", list_date=date(1991, 4, 3)),
            _instrument("600000.SH", list_date=date(1999, 11, 10)),
        ]
    ).write_parquet(curated_path)

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "instruments",
        run_id,
        "batch-0",
        pl.DataFrame(
            [
                _instrument("600519.SH", list_date=date(2001, 8, 27)),
                _instrument("000001.SZ", list_date=date(1991, 4, 3)),
            ]
        ),
    )

    rows = compact_instruments(cfg.staging_root, cfg.curated_root, run_id, trade_date)
    assert rows == 3

    merged = pl.read_parquet(curated_path)
    delisted = merged.filter(pl.col("symbol") == "600000.SH")
    assert delisted.height == 1
    assert delisted["delist_date"][0] == trade_date

    active = merged.filter(pl.col("symbol") == "600519.SH")
    assert active["delist_date"][0] is None


def test_compact_instruments_via_step_respects_manifest_gate(tmp_path):
    root = tmp_path / "data"
    cfg = Config(data_root=root)
    run_id = "run-step"
    trade_date = date(2024, 6, 28)

    writer = StagingWriter(cfg.staging_root)
    writer.write_batch(
        "instruments",
        run_id,
        "batch-0",
        pl.DataFrame([_instrument("600519.SH", list_date=date(2001, 8, 27))]),
    )

    step_compact(cfg, trade_date, run_id, {})
    assert (cfg.curated_root / "instruments" / "part-merged.parquet").exists()


def test_tradable_universe_excludes_delisted_symbol(tmp_path):
    root = tmp_path / "data"
    cfg = Config(data_root=root)
    curated_path = cfg.curated_root / "instruments" / "part-merged.parquet"
    curated_path.parent.mkdir(parents=True)
    pl.DataFrame(
        [
            _instrument("600519.SH", list_date=date(2001, 8, 27)),
            _instrument(
                "600000.SH",
                list_date=date(1999, 11, 10),
                delist_date=date(2024, 6, 27),
            ),
        ]
    ).write_parquet(curated_path)

    out = tradable_symbols_on_date(cfg, date(2024, 6, 28))
    assert out is not None
    assert set(out["symbol"].to_list()) == {"600519.SH"}


def test_enrich_instrument_list_dates_fills_nulls(tmp_path, monkeypatch):
    from stock_data_engine.adapters.eastmoney import instruments as em_inst

    cfg = Config(data_root=tmp_path / "data", sources={"eastmoney": True})
    df = pl.DataFrame([_instrument("600519.SH")])

    monkeypatch.setattr(
        em_inst,
        "fetch_list_date_map",
        lambda **kwargs: {"600519.SH": date(2001, 8, 27)},
    )

    enriched = em_inst.enrich_instrument_list_dates(cfg, df)
    assert enriched["list_date"][0] == date(2001, 8, 27)
