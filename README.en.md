# ashare-lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/rootSunc/ashare-lake/graph/badge.svg)](https://codecov.io/gh/rootSunc/ashare-lake)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md)

Self-hosted China A-share data layer: multi-source ingest, daily job
orchestration, and a curated Parquet lake with row-level provenance. Query it
directly with DuckDB or Polars — no database server, no TDX desktop client.

The CLI is `asl`, the package is `ashare_lake`. This repo is data-only:
backtesting and signal research stay downstream.

```
   tdx_protocol    eastmoney    sina    cninfo    baostock / akshare …
        │              │          │        │              │
        ▼              ▼          ▼        ▼              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  asl run daily · orchestration / watermarks / retry / audit │
  └────────────────────────────────────────────────────────────┘
                              │
       staging ──▶ curated ──▶ derived        Parquet with per-row
                              │               source / data_version / fetched_at
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
  Python load() API      DuckDB views / SQL    Polars scan_parquet
```

## Positioning: peers and trade-offs

AkShare / efinance solve fetching; this repo solves what comes after: many
adapters into one primary-key / partition / `load()` contract, as resumable,
provenance-tagged curated Parquet on disk. Details:
[comparison](docs/comparison.md) (Chinese).

| | ashare-lake | AkShare / efinance | Tushare Pro | Baostock / mootdx | Qlib / vn.py |
|--|-------------|-------------------|-------------|-------------------|--------------|
| Role | Self-hosted lake + daily jobs | Fetch helpers | Cloud API (credits) | Single-source API/protocol | Research / trading platform |
| Deliverable | Curated Parquet + `load()` | In-memory DataFrame | Remote tables | DataFrame | In-platform data |
| Orchestration / watermarks / retry | Yes | No | No | No | Platform-specific |
| Schema / provenance | Write-time checks + provenance cols | Usually none | Platform fields | No lake contract | Varies |
| Multi-source | Primary → curated; backup → snapshot only | Single call | Single vendor | Single source | Varies |

| Trade-off | Choice |
|-----------|--------|
| Source failure | Fail the batch — no silent fake data; `allow_mock` tests must tag `source="mock"` |
| Provenance | Every row: `source` / `data_version` / `fetched_at` |
| Adjustment | Store unadjusted bars; factors separate; compose `qfq` / `hfq` at query time |
| Failover | One curated row per PK; backups stay in snapshots; audit diffs; never auto-replace |
| Lookahead | Financials carry `announce_date`; use `load(..., as_of=)` for PIT |

## One-minute demo

Skip the full-market backfill. After install, one command fetches **5 liquid
names × ~30 trading days** of real TDX bars, prints phased progress, and shows
a sample table:

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"
asl demo
```

<p align="center">
  <img src="docs/assets/asl-demo.png" alt="asl demo: phased fetch with sample daily bars" width="820" />
</p>

Data lands in a separate `data/ashare-lake-demo/` root (safe beside a later full
`asl init`). Then:

```bash
asl query --config configs/ashare-lake.demo.toml --sql "
  SELECT symbol, trade_date, close, volume, source
  FROM daily_bars
  WHERE symbol = '600519.SH'
  ORDER BY trade_date DESC
  LIMIT 10
"
```

<p align="center">
  <img src="docs/assets/asl-query.png" alt="asl query: DuckDB SQL with provenance source column" width="720" />
</p>

Optional: `asl demo --symbols 600519.SH,000001.SZ --days 10`. Needs reachability
to TDX quote hosts (mainland egress is more reliable). On failure, try
`asl servers test`. Full-market daily ops still use Install / Quick start →
`asl init` below.

## Datasets

Dataset names are the first argument to `load()`. Columns and primary keys:
[schema](docs/datasets/schema.md); orchestration metadata:
[catalog](docs/datasets/catalog.md).

| Category | Datasets |
|----------|----------|
| Reference | `instruments` · `trading_calendar` · `trading_status` (suspensions / ST flags) |
| Market data | `daily_bars` (unadjusted) · `index_bars` · `adj_factors` |
| Corporate events | `corporate_actions` · `announcement_index` · `earnings_disclosure_schedule` |
| Fundamentals / valuation | `financial_statement_items` (PIT) · `valuation_metrics` · `analyst_consensus` |
| Capital flow | `fund_flow` · `margin_trading` · `northbound_flows` / `northbound_holdings` · `dragon_tiger` · `block_trades` · `institutional_holdings` |
| Structure / industry | `sector_members` · `index_constituents` · `industry_members` |
| Macro | `macro_indicators` · `market_breadth` |
| Sentiment / rotation | `sentiment_scores` · `hot_rank` · `sector_bars` · `sector_fund_flow` · `news_headlines` |
| Risk | `share_unlock_schedule` · `regulatory_events` |

## Install

Not on PyPI yet; install from source:

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"        # tdx = mootdx market-data source; add [dev] for development
```

With uv: `uv sync --extra tdx` (the repo ships a `uv.lock`).

Optional extras (`valuation` / `macro` / `nlp` / `structure`):
[installation](docs/getting-started/installation.md).

## Quick start

```bash
cp configs/ashare-lake.example.toml configs/ashare-lake.toml   # local config (gitignored)
asl init   --config configs/ashare-lake.toml                 # dirs / manifest / views + first backfill
asl run daily --config configs/ashare-lake.toml              # daily incremental
asl status --config configs/ashare-lake.toml
asl retry  --run-id <id> --config configs/ashare-lake.toml
```

After the initial backfill, run the acceptance checks in the
[runbook](docs/operations/runbook.md) (idempotency / semantics / coverage)
before wiring up cron:

```bash
.venv/bin/python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# re-run daily on the same window, then:
.venv/bin/python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

## Reading data

Python:

```python
from ashare_lake.query import load

bars = load(
    "daily_bars",
    start="2020-01-01", end="2025-12-31",
    adjust="hfq",              # None | "qfq" | "hfq"
    universe="all_a",          # see "Known limitations" below
)

roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

<p align="center">
  <img src="docs/assets/asl-load.png" alt="Python load(): read daily bars from local curated Parquet" width="720" />
</p>

DuckDB:

```bash
asl query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/ashare-lake.toml
```

You can also connect to `{data_root}/duckdb/ashare-lake.duckdb` directly
(views already disable unsafe hive parsing by partition granularity).

Or Polars on a **day-partitioned** dataset such as `daily_bars`:

```python
import polars as pl
bars = pl.scan_parquet("data/ashare-lake/curated/daily_bars/**/*.parquet")
df = bars.filter(pl.col("symbol") == "600519.SH").collect()
```

**Year/month partitions:** datasets like `index_bars`, `trading_calendar`,
`corporate_actions`, and `trading_status` use directory values `2024` /
`2024-06`, not full dates. DuckDB `read_parquet(..., hive_partitioning=true)`
(or Polars hive-parsing those labels as DATE) overwrites/conflicts with the
real date column in the file and looks like mass “duplicates.” Prefer
`asl query`, the published DuckDB views, or
`from ashare_lake.query import load, scan`. See
[lake-layout partition granularity](docs/architecture/lake-layout.md).

Lake layout:

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet
  derived/   adj_factors/...
  staging/   raw landing per run (cleanable after compact)
  meta/      manifest, quality findings, watermarks, on-demand cache
  duckdb/    ashare-lake.duckdb
```

## Known limitations

- **Survivorship bias:** daily bars can recover delisted names via baostock /
  `asl delisted backfill`, but you must also run `asl delisted repair` to write
  `delist_date` into `instruments`; otherwise `universe="all_a"` keeps selecting
  them. Widen coverage with `asl delisted discover` (includes old NEEQ prefixes
  43/83/87). Until that is complete, treat any return series with caution.
- `universe="all_a"` ST / suspension filtering only applies on dates covered by
  `trading_status` — the daily job fetches the current day only, so older
  windows are not filtered by historical ST status (backfill with
  `asl backfill`).
- BSE (BJ) bars come from Sina, not TDX (the TDX protocol has no BJ feed);
  BJ `amount` is null, and newly listed BJ names enter `instruments` only after
  `asl delisted discover` finds them.
- Some HTTP sources are unreliable outside mainland China; historical backfills
  such as `sector_bars` need a mainland egress or proxy — see the
  [runbook](docs/operations/runbook.md).
- Not published to PyPI yet; source install only.

## Project status

Currently [0.1.0](CHANGELOG.md) — the first public release of a data layer the
author runs daily on a personal cron. Dataset names, schemas, and the `load()`
signature may change before 1.0; everything is tracked in the
[CHANGELOG](CHANGELOG.md).

This is a personal project: issues and PRs are welcome, responses are
best-effort. See [CONTRIBUTING.md](CONTRIBUTING.md) (Chinese) before opening a
PR; security reports go through [SECURITY.md](SECURITY.md) (Chinese).
User-facing docs are Chinese-first; [CHANGELOG](CHANGELOG.md) and
[ADRs](docs/adr/) stay in English.

## Docs

Start at [docs/README.md](docs/README.md) (Chinese). Frequently used:
[comparison](docs/comparison.md) ·
[legal & data sources](docs/legal-and-data-sources.md) ·
[installation](docs/getting-started/installation.md) ·
[quickstart](docs/getting-started/quickstart.md) ·
[configuration](docs/getting-started/configuration.md) ·
[architecture](docs/architecture/overview.md) ·
[dataset catalog](docs/datasets/catalog.md) ·
[schema](docs/datasets/schema.md) ·
[query guide](docs/datasets/query-guide.md) ·
[runbook](docs/operations/runbook.md) ·
[CLI](docs/reference/cli.md) ·
[Python API](docs/reference/python-api.md)

## License

Code is [MIT](LICENSE). Market data and announcements you land locally remain
subject to upstream terms; this repo ships no data and grants no redistribution
rights — see [legal](docs/legal-and-data-sources.md).
