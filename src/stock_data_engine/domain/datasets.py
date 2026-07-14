"""Single source of truth for dataset metadata (DatasetSpec registry).

Every module that needs per-dataset knowledge — compact partitioning, watermark
policy, fetch semantics, query date columns, DuckDB views, audit — derives it
from ``DATASETS`` below. Schema and primary keys live in
``domain/schemas.py`` (polars dtypes); ``test_dataset_registry.py`` asserts the
two stay in sync.

Adding a dataset = one ``DatasetSpec`` entry here + schema/PK in schemas.py +
a registered step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FetchSemantics = Literal["by_date", "snapshot"]
Layer = Literal["curated", "derived"]


@dataclass(frozen=True)
class DatasetSpec:
    """Orchestration/query metadata for one dataset.

    partition_col:
        Hive partition directory key under the lake (None = merge-style single
        file, e.g. instruments).
    date_col:
        Column used for query date-range filters; defaults to ``partition_col``.
    fetch_semantics:
        ``by_date`` — source returns values for a requested day (gap catch-up
        allowed). ``snapshot`` — live page stamped with trade_date; historical
        replay would forge rows, so only the run day is ever fetched.
    watermark:
        Maintain a date watermark under ``meta/state`` (False for datasets
        partitioned by non-date keys like report_period).
    pit:
        Point-in-time dataset — ``load()`` requires ``as_of`` and filters on
        ``announce_date``.
    backfill_source:
        Name of an external historical source that can replay this dataset even
        though daily ``fetch_semantics`` is ``snapshot`` (e.g. valuation_metrics:
        EastMoney live snapshot daily, baostock for history). ``sde backfill``
        is allowed for snapshot datasets only when this is set.
    """

    name: str
    layer: Layer = "curated"
    partition_col: str | None = None
    date_col: str | None = None
    fetch_semantics: FetchSemantics = "by_date"
    watermark: bool = True
    pit: bool = False
    backfill_source: str | None = None
    # How many days the freshest data may lag the last trading day before it is
    # flagged STALE. 1 tolerates normal T+1 EOD publication; larger values mark
    # sources with a slower cadence (margin T+1, quarterly northbound holdings)
    # so their inherent lag is not mistaken for a stuck pipeline.
    max_staleness_days: int = 1

    @property
    def query_date_col(self) -> str | None:
        return self.date_col or self.partition_col


_SPECS = [
    # L0 reference
    DatasetSpec("instruments", partition_col=None, watermark=False),
    DatasetSpec("trading_calendar", partition_col="trade_date"),
    DatasetSpec("trading_status", partition_col="trade_date"),
    # L1 bars
    DatasetSpec("daily_bars", partition_col="trade_date"),
    DatasetSpec("index_bars", partition_col="trade_date"),
    # L2 corporate events
    DatasetSpec("corporate_actions", partition_col="ex_date"),
    DatasetSpec("announcement_index", partition_col="announce_date", pit=True),
    # L3 fundamentals
    DatasetSpec(
        "financial_statement_items",
        partition_col="report_period",
        watermark=False,
        pit=True,
    ),
    DatasetSpec(
        "valuation_metrics",
        partition_col="trade_date",
        fetch_semantics="snapshot",
        backfill_source="baostock",
    ),
    DatasetSpec("analyst_consensus", partition_col="forecast_date", fetch_semantics="snapshot"),
    # L4 capital flows
    DatasetSpec("fund_flow", partition_col="trade_date", fetch_semantics="snapshot"),
    DatasetSpec("margin_trading", partition_col="trade_date", max_staleness_days=2),
    # Per-stock northbound holdings are quarterly since Aug 2024; tolerate the
    # gap to the next quarter-end before flagging stale.
    DatasetSpec("northbound_holdings", partition_col="trade_date", max_staleness_days=100),
    DatasetSpec("northbound_flows", partition_col="trade_date", max_staleness_days=2),
    DatasetSpec("dragon_tiger", partition_col="trade_date"),
    DatasetSpec("block_trades", partition_col="trade_date"),
    DatasetSpec("institutional_holdings", partition_col="report_period", watermark=False),
    # L5 structure
    DatasetSpec("sector_members", partition_col="as_of_date", fetch_semantics="snapshot"),
    DatasetSpec("index_constituents", partition_col="as_of_date", fetch_semantics="snapshot"),
    DatasetSpec("industry_members", partition_col="as_of_date", fetch_semantics="snapshot"),
    # L6 macro
    DatasetSpec("macro_indicators", partition_col="obs_date"),
    DatasetSpec("market_breadth", partition_col="trade_date"),
    # L7 sentiment / rotation
    DatasetSpec("sentiment_scores", partition_col="trade_date"),
    DatasetSpec("hot_rank", partition_col="trade_date", fetch_semantics="snapshot"),
    DatasetSpec(
        "sector_bars",
        partition_col="trade_date",
        fetch_semantics="snapshot",
        backfill_source="eastmoney_kline",
    ),
    DatasetSpec("sector_fund_flow", partition_col="trade_date", fetch_semantics="snapshot"),
    DatasetSpec("news_headlines", partition_col="publish_date", fetch_semantics="snapshot"),
    DatasetSpec("flash_news_wire", partition_col="publish_date", fetch_semantics="snapshot"),
    DatasetSpec("economic_calendar", partition_col="event_date", fetch_semantics="snapshot"),
    # L8 risk
    DatasetSpec("share_unlock_schedule", partition_col="unlock_date"),
    DatasetSpec("regulatory_events", partition_col="event_date"),
    # derived
    DatasetSpec("adj_factors", layer="derived", partition_col="trade_date"),
]

DATASETS: dict[str, DatasetSpec] = {spec.name: spec for spec in _SPECS}


def get_dataset(name: str) -> DatasetSpec:
    try:
        return DATASETS[name]
    except KeyError:
        raise KeyError(f"unknown dataset {name!r}") from None


def curated_dataset_names() -> frozenset[str]:
    return frozenset(s.name for s in DATASETS.values() if s.layer == "curated")


def derived_dataset_names() -> frozenset[str]:
    return frozenset(s.name for s in DATASETS.values() if s.layer == "derived")


def pit_dataset_names() -> frozenset[str]:
    return frozenset(s.name for s in DATASETS.values() if s.pit)


def fetch_semantics(dataset: str) -> FetchSemantics:
    spec = DATASETS.get(dataset)
    return spec.fetch_semantics if spec else "by_date"


def is_stale(dataset: str, mark, anchor) -> bool:
    """Whether *dataset*'s freshest date (*mark*) lags *anchor* beyond tolerance.

    *mark* and *anchor* are ``datetime.date`` (or None). A dataset with no mark
    is not judged here (callers treat empty separately).
    """
    if mark is None or anchor is None:
        return False
    spec = DATASETS.get(dataset)
    tolerance = spec.max_staleness_days if spec else 1
    return (anchor - mark).days > tolerance


# ---------------------------------------------------------------------------
# Derived legacy tables (kept so existing imports stay valid; do not edit these
# directly — edit the DatasetSpec entries above).
# ---------------------------------------------------------------------------

# partition column per curated dataset; None = merge-style (e.g. instruments).
PARTITION_COLS: dict[str, str | None] = {
    s.name: s.partition_col for s in DATASETS.values() if s.layer == "curated"
}

FETCH_SEMANTICS: dict[str, FetchSemantics] = {
    s.name: s.fetch_semantics
    for s in DATASETS.values()
    if s.fetch_semantics != "by_date"
}

# Datasets partitioned by non-date keys — skip date-based watermarks.
WATERMARK_SKIP = frozenset(
    s.name
    for s in DATASETS.values()
    if s.layer == "curated" and s.partition_col is not None and not s.watermark
)

# Warn when a partition's row/symbol count falls below this fraction of the prior partition.
ROW_COUNT_MUTATION_MIN_RATIO = 0.5

# Ignore mutation checks when the baseline partition is smaller than this.
ROW_COUNT_MUTATION_MIN_BASELINE_ROWS = 50
