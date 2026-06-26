from __future__ import annotations

from pathlib import Path

import duckdb

from stock_data_engine.config import Config

_EMPTY_VIEW_DDL = {
    "daily_bars": """
        CREATE OR REPLACE VIEW daily_bars AS
        SELECT
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS DATE) AS trade_date,
            CAST(NULL AS DOUBLE) AS open,
            CAST(NULL AS DOUBLE) AS high,
            CAST(NULL AS DOUBLE) AS low,
            CAST(NULL AS DOUBLE) AS close,
            CAST(NULL AS BIGINT) AS volume,
            CAST(NULL AS DOUBLE) AS amount,
            CAST(NULL AS VARCHAR) AS source,
            CAST(NULL AS VARCHAR) AS data_version,
            CAST(NULL AS VARCHAR) AS fetched_at
        WHERE false
    """,
    "instruments": """
        CREATE OR REPLACE VIEW instruments AS
        SELECT
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS VARCHAR) AS name,
            CAST(NULL AS VARCHAR) AS exchange,
            CAST(NULL AS VARCHAR) AS asset_type,
            CAST(NULL AS DATE) AS list_date,
            CAST(NULL AS DATE) AS delist_date,
            CAST(NULL AS VARCHAR) AS prev_symbol,
            CAST(NULL AS VARCHAR) AS source,
            CAST(NULL AS VARCHAR) AS data_version,
            CAST(NULL AS VARCHAR) AS fetched_at
        WHERE false
    """,
    "adj_factors": """
        CREATE OR REPLACE VIEW adj_factors AS
        SELECT
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS DATE) AS trade_date,
            CAST(NULL AS VARCHAR) AS adjust_type,
            CAST(NULL AS DOUBLE) AS factor,
            CAST(NULL AS VARCHAR) AS source,
            CAST(NULL AS VARCHAR) AS data_version,
            CAST(NULL AS VARCHAR) AS fetched_at
        WHERE false
    """,
}


def _glob_has_files(pattern: str) -> bool:
    base = pattern.split("**")[0].rstrip("/")
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

    view_defs = {
        "daily_bars": f"{root}/curated/daily_bars/**/*.parquet",
        "instruments": f"{root}/curated/instruments/*.parquet",
        "adj_factors": f"{root}/derived/adj_factors/**/*.parquet",
    }

    for view_name, glob_path in view_defs.items():
        hive = "true" if "**" in glob_path else "false"
        if _glob_has_files(glob_path) or require_data:
            con.execute(
                f"""
                CREATE OR REPLACE VIEW {view_name} AS
                SELECT * FROM read_parquet('{glob_path}', hive_partitioning={hive})
                """
            )
        else:
            con.execute(_EMPTY_VIEW_DDL[view_name])

    con.execute(
        """
        CREATE OR REPLACE VIEW daily_bars_adj AS
        SELECT b.*, b.close * COALESCE(a.factor, 1.0) AS adj_close
        FROM daily_bars b
        LEFT JOIN adj_factors a
          ON b.symbol = a.symbol AND b.trade_date = a.trade_date AND a.adjust_type = 'qfq'
        """
    )
    con.close()
    return db_path
