"""Invariants tying the DatasetSpec registry to schemas, PKs, and steps."""

from stock_data_engine.domain.datasets import (
    DATASETS,
    FETCH_SEMANTICS,
    PARTITION_COLS,
    WATERMARK_SKIP,
    curated_dataset_names,
    derived_dataset_names,
    fetch_semantics,
    get_dataset,
    pit_dataset_names,
)
from stock_data_engine.domain.schemas import DATASET_SCHEMAS, PRIMARY_KEYS


def test_every_dataset_has_schema_and_pk():
    for name in DATASETS:
        assert name in DATASET_SCHEMAS, f"{name} missing from DATASET_SCHEMAS"
        assert name in PRIMARY_KEYS, f"{name} missing from PRIMARY_KEYS"


def test_every_schema_has_registry_entry():
    for name in DATASET_SCHEMAS:
        assert name in DATASETS, f"{name} in DATASET_SCHEMAS but not in registry"


def test_partition_and_date_cols_exist_in_schema():
    for name, spec in DATASETS.items():
        schema = DATASET_SCHEMAS[name]
        if spec.partition_col is not None:
            assert spec.partition_col in schema, (
                f"{name}: partition_col {spec.partition_col!r} not in schema"
            )
        if spec.query_date_col is not None:
            assert spec.query_date_col in schema, (
                f"{name}: date_col {spec.query_date_col!r} not in schema"
            )


def test_primary_key_columns_exist_in_schema():
    for name, pk in PRIMARY_KEYS.items():
        schema = DATASET_SCHEMAS[name]
        for col in pk:
            assert col in schema, f"{name}: PK column {col!r} not in schema"


def test_legacy_tables_match_registry():
    # Guards against editing the derived dicts instead of the specs.
    assert set(PARTITION_COLS) == set(curated_dataset_names())
    assert WATERMARK_SKIP == {"financial_statement_items", "institutional_holdings"}
    assert set(FETCH_SEMANTICS) == {
        "fund_flow",
        "valuation_metrics",
        "sector_members",
        "index_constituents",
        "industry_members",
    }
    assert fetch_semantics("fund_flow") == "snapshot"
    assert fetch_semantics("daily_bars") == "by_date"


def test_layer_partitions():
    assert "adj_factors" in derived_dataset_names()
    assert "adj_factors" not in curated_dataset_names()
    assert pit_dataset_names() == {"financial_statement_items", "announcement_index"}
    assert get_dataset("daily_bars").partition_col == "trade_date"


def test_registered_fetch_steps_cover_curated_datasets():
    """Every curated dataset is producible by a registered step."""
    import stock_data_engine.steps  # noqa: F401 — register steps
    from stock_data_engine.orchestrator.registry import STEP_REGISTRY

    # market_breadth/sentiment_scores are derive-style steps registered under
    # their dataset names; instruments etc. match step names directly.
    missing = [name for name in curated_dataset_names() if name not in STEP_REGISTRY]
    assert not missing, f"curated datasets without a registered step: {missing}"
