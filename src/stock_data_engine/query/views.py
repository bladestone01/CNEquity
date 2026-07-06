"""DuckDB view layer — one view per dataset, generated from the registry."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.domain.datasets import DATASETS, DatasetSpec
from stock_data_engine.domain.schemas import DATASET_SCHEMAS


def _duckdb_type(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Datetime):
        return "TIMESTAMPTZ" if dtype.time_zone else "TIMESTAMP"
    if dtype == pl.Date:
        return "DATE"
    if dtype == pl.Boolean:
        return "BOOLEAN"
    if dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64):
        return "BIGINT"
    if dtype in (pl.Float32, pl.Float64):
        return "DOUBLE"
    return "VARCHAR"


def _empty_view_sql(name: str) -> str:
    schema = DATASET_SCHEMAS[name]
    cols = ",\n            ".join(
        f"CAST(NULL AS {_duckdb_type(dtype)}) AS {col}" for col, dtype in schema.items()
    )
    return f"""
        CREATE OR REPLACE VIEW {name} AS
        SELECT
            {cols}
        WHERE false
    """


def _view_glob(data_root: str, spec: DatasetSpec) -> tuple[str, bool]:
    layer_dir = "derived" if spec.layer == "derived" else "curated"
    if spec.partition_col is None:
        return f"{data_root}/{layer_dir}/{spec.name}/*.parquet", False
    return f"{data_root}/{layer_dir}/{spec.name}/**/*.parquet", True


def _glob_has_files(pattern: str) -> bool:
    base = pattern.split("**")[0].split("*")[0].rstrip("/")
    p = Path(base)
    if not p.exists():
        return False
    return any(p.rglob("*.parquet"))


def ensure_duckdb_views(config: Config, *, require_data: bool = False) -> Path:
    db_path = config.duckdb_path or (config.data_root / "duckdb" / "stockdata.duckdb")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    root = str(config.data_root).replace("'", "''")

    con = duckdb.connect(str(db_path))
    con.execute(f"SET memory_limit='{config.duckdb_memory_limit}'")
    con.execute(f"SET threads={config.duckdb_threads}")

    for name, spec in sorted(DATASETS.items()):
        glob_path, hive = _view_glob(root, spec)
        if _glob_has_files(glob_path) or require_data:
            con.execute(
                f"""
                CREATE OR REPLACE VIEW {name} AS
                SELECT * FROM read_parquet('{glob_path}', hive_partitioning={str(hive).lower()})
                """
            )
        else:
            con.execute(_empty_view_sql(name))

    # Adjusted bars per ADR-0004: only hfq factors are stored.
    #   hfq price = raw * factor
    #   qfq price = raw * factor / anchor   (anchor = symbol's latest hfq factor)
    # adj_* keeps its historical qfq meaning; adj_is_exact mirrors the Python API.
    con.execute(
        """
        CREATE OR REPLACE VIEW daily_bars_adj AS
        WITH hfq AS (
            SELECT symbol, trade_date, factor
            FROM adj_factors
            WHERE adjust_type = 'hfq'
        ),
        anchors AS (
            SELECT symbol, factor AS hfq_anchor
            FROM hfq
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
        )
        SELECT
            b.*,
            h.factor IS NOT NULL AS adj_is_exact,
            b.open  * COALESCE(h.factor, 1.0) AS hfq_open,
            b.high  * COALESCE(h.factor, 1.0) AS hfq_high,
            b.low   * COALESCE(h.factor, 1.0) AS hfq_low,
            b.close * COALESCE(h.factor, 1.0) AS hfq_close,
            b.open  * COALESCE(h.factor / a.hfq_anchor, 1.0) AS qfq_open,
            b.high  * COALESCE(h.factor / a.hfq_anchor, 1.0) AS qfq_high,
            b.low   * COALESCE(h.factor / a.hfq_anchor, 1.0) AS qfq_low,
            b.close * COALESCE(h.factor / a.hfq_anchor, 1.0) AS qfq_close,
            b.close * COALESCE(h.factor / a.hfq_anchor, 1.0) AS adj_close
        FROM daily_bars b
        LEFT JOIN hfq h
          ON b.symbol = h.symbol AND b.trade_date = h.trade_date
        LEFT JOIN anchors a
          ON b.symbol = a.symbol
        """
    )
    con.close()
    return db_path
