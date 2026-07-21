"""Unit tests for commodity_bars adapter normalize path."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl

from ashare_lake.adapters.eastmoney.commodity_bars import (
    CONTINUOUS_CONTRACTS,
    fetch_commodity_bars_range,
)
from ashare_lake.domain.datasets import DATASETS, get_dataset
from ashare_lake.domain.schemas import DATASET_SCHEMAS, PRIMARY_KEYS, validate_dataframe


def test_commodity_bars_registered():
    spec = get_dataset("commodity_bars")
    assert spec.partition_col == "trade_date"
    assert spec.fetch_semantics == "by_date"
    assert spec.backfill_source == "eastmoney_kline+sina_global"
    assert spec.required is False
    assert "commodity_bars" in DATASET_SCHEMAS
    assert PRIMARY_KEYS["commodity_bars"] == ["symbol", "trade_date"]


def test_continuous_contract_symbols_unique():
    syms = [c[0] for c in CONTINUOUS_CONTRACTS]
    assert len(syms) == len(set(syms))
    for sym, secid, _name, exch in CONTINUOUS_CONTRACTS:
        assert sym.endswith(f".{exch}")
        assert "." in secid


def test_fetch_commodity_bars_parses_kline():
    kline_body = {
        "data": {
            "name": "沪金主连",
            "klines": [
                "2026-07-20,880.0,885.0,890.0,870.0,1000,123456.0,1.2",
                "2026-07-21,885.0,892.4,893.5,875.0,1286,234567.0,1.1",
            ],
        }
    }

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return kline_body

    fake_client = MagicMock()
    fake_client.get.return_value = FakeResp()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None

    only = (("AU0.SHF", "113.AUM", "沪金主连", "SHF"),)
    with patch(
        "ashare_lake.adapters.eastmoney.commodity_bars.EastMoneyClient",
        return_value=fake_client,
    ), patch(
        "ashare_lake.adapters.sina.global_futures.fetch_offshore_commodity_bars_range",
        return_value=pl.DataFrame(),
    ):
        df = fetch_commodity_bars_range(
            date(2026, 7, 20),
            date(2026, 7, 21),
            contracts=only,
            include_offshore=True,
        )

    assert df.height == 2
    assert set(df["symbol"].to_list()) == {"AU0.SHF"}
    assert df.filter(pl.col("trade_date") == date(2026, 7, 21))["close"][0] == 892.4
    from ashare_lake.domain.schemas import with_provenance

    validated = validate_dataframe(
        with_provenance(df, source="eastmoney", data_version="v1"),
        "commodity_bars",
    )
    assert validated.height == 2
    assert "open_interest" in validated.columns


def test_fetch_commodity_bars_empty_on_failure():
    fake_client = MagicMock()
    fake_client.get.side_effect = RuntimeError("boom")
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None

    only = (("AU0.SHF", "113.AUM", "沪金主连", "SHF"),)
    with patch(
        "ashare_lake.adapters.eastmoney.commodity_bars.EastMoneyClient",
        return_value=fake_client,
    ), patch(
        "ashare_lake.adapters.sina.global_futures.fetch_offshore_commodity_bars_range",
        return_value=pl.DataFrame(),
    ):
        df = fetch_commodity_bars_range(
            date(2026, 7, 21),
            date(2026, 7, 21),
            contracts=only,
            include_offshore=False,
        )
    assert df.is_empty()


def test_dataset_count_includes_commodity():
    assert "commodity_bars" in DATASETS
