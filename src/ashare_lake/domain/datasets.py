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
from datetime import date
from typing import Literal

from ashare_lake.domain.partitions import Granularity, partition_value

FetchSemantics = Literal["by_date", "snapshot"]
HistoryMode = Literal["by_date", "snapshot_with_backfill", "snapshot_only"]
Layer = Literal["curated", "derived"]


@dataclass(frozen=True)
class DatasetSpec:
    """Orchestration/query metadata for one dataset.

    partition_col:
        Hive partition directory key under the lake (None = merge-style single
        file, e.g. instruments).
    partition_granularity:
        Period each partition directory covers — ``day``, ``month`` or ``year``.
        Pick it from rows per day, not from habit: a Parquet footer costs ~1KB
        whatever it holds, so a dataset with a handful of rows a day spends
        almost all its bytes and all its file opens on metadata. Rough bands
        used here: ≥1000 rows/day → ``day``, 50–1000 → ``month``, <50 → ``year``.
        Only ``day`` values can be hive-parsed (see domain/partitions.py).
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
        EastMoney live snapshot daily, baostock for history). ``asl backfill``
        is allowed for snapshot datasets only when this is set.
    required:
        When False, an empty curated root is a warning (not an error) and does
        not alone make ``lake_health`` UNHEALTHY. Use for registered datasets
        whose source is not yet wired or is temporarily unavailable.
    history_horizon_days:
        Trading days of history the *source* still serves, counted back from
        today. ``None`` (the default) means the source has no such limit and
        history is bounded only by what has been backfilled.

        This is a property of the vendor, not of this lake, and it is the
        difference between "not fetched yet" and "can never be fetched". TDX
        keeps 95 trading days of 1-minute bars and 491 of 5-minute and coarser;
        asking for 2016 does not return less data, it returns none, and no
        backfill source extends it. ``by_date`` alone would promise a decade.
    """

    name: str
    layer: Layer = "curated"
    partition_col: str | None = None
    partition_granularity: Granularity = "day"
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
    required: bool = True
    history_horizon_days: int | None = None
    # Calendar days of history one backfill sub-run may cover. None = one run
    # for the whole window, which is what every daily-cadence dataset wants.
    #
    # Set it where a full window's staging would not fit in memory: compact
    # reads *every* staging file of a run into one frame, so a 95-day
    # full-market minute_bars seed (~123M rows) has to be drained in slices.
    # Chunking also makes a killed sweep resumable at the last compacted slice
    # instead of losing everything.
    backfill_chunk_days: int | None = None

    @property
    def query_date_col(self) -> str | None:
        return self.date_col or self.partition_col

    def earliest_available(self, today: date, *, trading_days_per_year: int = 242) -> date | None:
        """Rough calendar date before which the source serves nothing.

        The horizon is counted in *trading* days because that is how the vendor
        caps it (a fixed bar count), so it is converted with the usual ~242
        sessions a year. Deliberately approximate and deliberately early: it
        guards a CLI window, and refusing a window the source would in fact
        have served is worse than fetching a few empty days.
        """
        if self.history_horizon_days is None:
            return None
        calendar_days = round(self.history_horizon_days * 365 / trading_days_per_year)
        return date.fromordinal(max(1, today.toordinal() - calendar_days))

    def partition_for(self, d: date) -> str:
        """Directory value of the partition holding *d* for this dataset."""
        return partition_value(d, self.partition_granularity)


_SPECS = [
    # L0 reference
    # Live sources (TDX/EM) only list what trades today; baostock's stock_basic
    # is what recovers delisted codes, so `asl backfill instruments` is the only
    # path to a survivorship-free universe.
    DatasetSpec(
        "instruments",
        partition_col=None,
        watermark=False,
        backfill_source="baostock",
    ),
    DatasetSpec("trading_calendar", partition_col="trade_date", partition_granularity="year"),
    DatasetSpec("trading_status", partition_col="trade_date", partition_granularity="month"),
    # L1 bars
    DatasetSpec("daily_bars", partition_col="trade_date"),
    DatasetSpec("index_bars", partition_col="trade_date", partition_granularity="year"),
    # 1-minute bars. Day partitions: ~240 bars × the configured scope, which is
    # 1.3M rows a day at full market — the top of the ≥1000 rows/day band, and
    # ~30MB a partition. The schema draft once sketched
    # frequency/trade_date/symbol_bucket; a second directory level buys nothing
    # at that size and every partition-aware module here assumes exactly one.
    #
    # Opt-in (required=False): this is not on the default daily waves and a lake
    # that never enabled it must not be judged unhealthy for holding no rows.
    DatasetSpec(
        "minute_bars",
        partition_col="trade_date",
        partition_granularity="day",
        fetch_semantics="by_date",
        required=False,
        # Measured 2026-08-01 against 120.76.1.198:7709 — 22,800 bars for every
        # symbol probed, across both exchanges and every liquidity band, so it
        # is a server retention window rather than a per-symbol artefact.
        history_horizon_days=95,
        # ~7 trading days of full-market 1m per sub-run: ~9M rows staged, which
        # compacts comfortably, against ~123M for the whole horizon at once.
        backfill_chunk_days=10,
    ),
    # Domestic commodity futures main-continuous (东财主连) + narrow offshore
    # gold (Sina COMEX ``GC0.CMX``); not A-share equity.
    DatasetSpec(
        "commodity_bars",
        partition_col="trade_date",
        partition_granularity="year",
        fetch_semantics="by_date",
        backfill_source="eastmoney_kline+sina_global",
        required=False,
        max_staleness_days=2,
    ),
    # L2 corporate events
    DatasetSpec("corporate_actions", partition_col="ex_date", partition_granularity="year"),
    DatasetSpec("announcement_index", partition_col="announce_date", pit=True),
    # Current-state timetable (revisions overwrite scheduled_date; not PIT).
    DatasetSpec(
        "earnings_disclosure_schedule",
        partition_col="report_period",
        watermark=False,
    ),
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
    DatasetSpec(
        "northbound_flows",
        partition_col="trade_date",
        partition_granularity="year",
        max_staleness_days=2,
    ),
    DatasetSpec("dragon_tiger", partition_col="trade_date", partition_granularity="month"),
    DatasetSpec("block_trades", partition_col="trade_date", partition_granularity="month"),
    DatasetSpec("institutional_holdings", partition_col="report_period", watermark=False),
    # L5 structure
    DatasetSpec("sector_members", partition_col="as_of_date", fetch_semantics="snapshot"),
    DatasetSpec(
        "index_constituents",
        partition_col="as_of_date",
        partition_granularity="month",
        fetch_semantics="snapshot",
        # CNI adjustment history reconstructs 399001/399006 from ~2021-12;
        # CSI indices still accumulate via daily EM snapshots only.
        backfill_source="cni",
    ),
    DatasetSpec(
        "industry_members",
        partition_col="as_of_date",
        fetch_semantics="snapshot",
        # Shenwan StockClassifyUse intervals → monthly as_of from 2020.
        backfill_source="sw",
    ),
    # L6 macro
    DatasetSpec("macro_indicators", partition_col="obs_date", partition_granularity="year"),
    DatasetSpec("market_breadth", partition_col="trade_date", partition_granularity="year"),
    # L7 sentiment / rotation
    DatasetSpec("sentiment_scores", partition_col="trade_date", partition_granularity="month"),
    DatasetSpec(
        "hot_rank",
        partition_col="trade_date",
        partition_granularity="month",
        fetch_semantics="snapshot",
    ),
    DatasetSpec(
        "sector_bars",
        partition_col="trade_date",
        partition_granularity="month",
        fetch_semantics="snapshot",
        # 同花顺 per-year board-kline files (adapters/ths/boards.sweep_board_bars),
        # not EastMoney: the source was migrated to a single 同花顺 base to end the
        # mixed-source basis breaks, and this label had not followed.
        backfill_source="ths",
    ),
    DatasetSpec(
        "sector_fund_flow",
        partition_col="trade_date",
        partition_granularity="month",
        fetch_semantics="snapshot",
    ),
    DatasetSpec(
        "news_headlines",
        partition_col="publish_date",
        partition_granularity="month",
        fetch_semantics="snapshot",
    ),
    DatasetSpec(
        "flash_news_wire",
        partition_col="publish_date",
        partition_granularity="month",
        fetch_semantics="snapshot",
    ),
    # EM datacenter report RPT_ECONOMICCALENDAR was retired (code 9501); keep the
    # schema/registry for a replacement source, but do not fail lake health.
    DatasetSpec(
        "economic_calendar",
        partition_col="event_date",
        partition_granularity="year",
        fetch_semantics="snapshot",
        required=False,
    ),
    # L8 risk
    DatasetSpec("share_unlock_schedule", partition_col="unlock_date", partition_granularity="year"),
    DatasetSpec("regulatory_events", partition_col="event_date", partition_granularity="year"),
    # derived
    DatasetSpec("adj_factors", layer="derived", partition_col="trade_date"),
    # Industry returns computed from 申万 membership × hfq bars rather than
    # fetched, so index and constituents cannot disagree. Yearly partitions:
    # ~3 levels × 2 weightings × ~500 industries a day.
    DatasetSpec(
        "industry_index",
        layer="derived",
        partition_col="trade_date",
        partition_granularity="year",
    ),
    # How each recovered delisting's price series ends — see
    # DELISTING_EVENTS_SCHEMA. Merge-style: one row per symbol, a few hundred
    # rows total. date_col (not partition_col) so load(start=/end=) still filters.
    DatasetSpec(
        "delisting_events",
        layer="derived",
        partition_col=None,
        date_col="last_trade_date",
        watermark=False,
    ),
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


def history_mode_for(spec: DatasetSpec) -> HistoryMode:
    """Whether the dataset can expose an honest historical series.

    Derived only from registry fields (no parallel flags):
    - ``by_date`` — gap-fill / date-walk
    - ``snapshot_with_backfill`` — daily tip snapshot + dedicated history source
    - ``snapshot_only`` — tip-only; no honest historical replay
    """
    if spec.fetch_semantics == "by_date":
        return "by_date"
    if spec.backfill_source:
        return "snapshot_with_backfill"
    return "snapshot_only"


def history_mode(dataset: str) -> HistoryMode:
    spec = DATASETS.get(dataset)
    if spec is None:
        return "by_date"
    return history_mode_for(spec)


def granularity_for_dataset(dataset: str) -> Granularity:
    spec = DATASETS.get(dataset)
    return spec.partition_granularity if spec else "day"


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
    s.name: s.fetch_semantics for s in DATASETS.values() if s.fetch_semantics != "by_date"
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
