# ashare-lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
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
before wiring up cron.

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

DuckDB (`asl query --sql "..."` or connect to
`{data_root}/duckdb/ashare-lake.duckdb` directly), or Polars straight off the
Parquet files:

```python
import polars as pl
bars = pl.scan_parquet("data/ashare-lake/curated/daily_bars/**/*.parquet")
df = bars.filter(pl.col("symbol") == "600519.SH").collect()
```

Lake layout:

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet
  derived/   adj_factors/...
  staging/   raw landing per run (cleanable after compact)
  meta/      manifest, quality findings, watermarks, on-demand cache
  duckdb/    ashare-lake.duckdb
```

## Design choices

- A failed source fails the batch — no silent fake rows; test-only `allow_mock`
  data must be tagged `source="mock"`
- Every row carries `source` / `data_version` / `fetched_at`, so bad data is
  traceable to a source and batch
- Daily bars are stored unadjusted; adjustment factors live separately and
  qfq / hfq are composed at query time
- One row per primary key in curated; secondary sources go to snapshots and
  audits emit diffs — they never silently replace the primary source
- Low-frequency data (financials etc.) carries `announce_date`; use
  `load(..., as_of=)` for point-in-time views without lookahead

## Known limitations

- `universe="all_a"` ST / suspension filtering only applies on dates covered by
  `trading_status` — the daily job fetches the current day only, so older
  windows are not filtered by historical ST status (backfill with
  `asl backfill`).
- Some HTTP sources are unreliable outside mainland China; historical backfills
  such as `sector_bars` need a mainland egress or proxy — see the
  [runbook](docs/operations/runbook.md).
- Not published to PyPI yet; source install only.

## How it differs from AkShare / Tushare / Baostock

AkShare / efinance solve fetching; this project solves what comes after: many
adapters normalized into one primary-key / partition / `load()` contract, with
the data as resumable, provenance-tagged curated Parquet on your own disk.
Tushare Pro sits behind a credit wall; Baostock / mootdx are single-source with
incompatible schemas; Qlib / vn.py bundle a full research platform. Detailed
comparison: [docs/comparison.md](docs/comparison.md).

## Project status

Currently [0.1.0](CHANGELOG.md) — the first public release of a data layer the
author runs daily on a personal cron. Dataset names, schemas, and the `load()`
signature may change before 1.0; everything is tracked in the
[CHANGELOG](CHANGELOG.md).

This is a personal project: issues and PRs are welcome, responses are
best-effort. See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR;
security reports go through [SECURITY.md](SECURITY.md).

## Docs

Start at [docs/README.md](docs/README.md). Frequently used:
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
[Python API](docs/reference/python-api.md) ·
[ADRs](docs/adr/) · [CHANGELOG](CHANGELOG.md)

## License

Code is [MIT](LICENSE). Market data and announcements you land locally remain
subject to upstream terms; this repo ships no data and grants no redistribution
rights — see [legal](docs/legal-and-data-sources.md).
