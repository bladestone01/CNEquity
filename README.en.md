# ashare-lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/rootSunc/ashare-lake/graph/badge.svg)](https://codecov.io/gh/rootSunc/ashare-lake)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md)

**Self-hosted China A-share data lake**: multi-source ingest → daily jobs →
curated Parquet with row-level provenance.  
Query with DuckDB / Polars / `load()` — no database server, no TDX desktop client.

CLI: `asl` · package: `ashare_lake` · **data layer only** — backtests stay
downstream.

### Why this repo

- ✅ **Real data in one minute**: `asl demo` pulls live TDX bars — not a toy mock
- ✅ **Resumable daily jobs**: watermarks / retry / quality audit — cron-ready
- ✅ **Row-level provenance**: every row has `source` / `data_version` / `fetched_at`
- ✅ **One contract**: many adapters → one PK / partition / `load()` API
- ✅ **Zero-friction query**: Python `load()` · DuckDB SQL · Polars on Parquet
- ✅ **Not a backtester**: fills the gap *after* AkShare / efinance fetch

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

## One-minute demo

Skip the full-market backfill. One command fetches **5 liquid names × ~30
trading days** of real bars:

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

Data lands in `data/ashare-lake-demo/` (safe beside a later full `asl init`).
Then:

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
`asl servers test`.

## Positioning: peers

AkShare / efinance solve fetching; this repo solves what comes after: many
adapters into one primary-key / partition / `load()` contract, as resumable,
provenance-tagged curated Parquet. Details: [comparison](docs/comparison.md)
(Chinese).

| | ashare-lake | AkShare / efinance | Tushare Pro | Baostock / mootdx | Qlib / vn.py |
|--|-------------|-------------------|-------------|-------------------|--------------|
| Role | Self-hosted lake + daily jobs | Fetch helpers | Cloud API (credits) | Single-source API/protocol | Research / trading platform |
| Deliverable | Curated Parquet + `load()` | In-memory DataFrame | Remote tables | DataFrame | In-platform data |
| Orchestration / watermarks / retry | Yes | No | No | No | Platform-specific |
| Schema / provenance | Write-time checks + provenance cols | Usually none | Platform fields | No lake contract | Varies |
| Multi-source | Primary → curated; backup → snapshot only | Single call | Single vendor | Single source | Varies |

**Trade-offs (short):** fail the batch on source errors · store unadjusted bars
+ separate factors · never auto-replace curated with backups · financials carry
`announce_date` for PIT. Full list in [comparison](docs/comparison.md).

## Datasets

Dataset names are the first argument to `load()`. Columns:
[schema](docs/datasets/schema.md); orchestration metadata:
[catalog](docs/datasets/catalog.md).

| Category | Datasets |
|----------|----------|
| Reference | `instruments` · `trading_calendar` · `trading_status` (suspensions / ST) |
| Market data | `daily_bars` (unadjusted) · `index_bars` · `adj_factors` |
| Corporate events | `corporate_actions` · `announcement_index` · `earnings_disclosure_schedule` |
| Fundamentals / valuation | `financial_statement_items` (PIT) · `valuation_metrics` · `analyst_consensus` |
| Capital flow | `fund_flow` · `margin_trading` · `northbound_flows` / `northbound_holdings` · `dragon_tiger` · `block_trades` · `institutional_holdings` |
| Structure / industry | `sector_members` · `index_constituents` · `industry_members` |
| Macro | `macro_indicators` · `market_breadth` |
| Sentiment / rotation | `sentiment_scores` · `hot_rank` · `sector_bars` · `sector_fund_flow` · `news_headlines` |
| Risk | `share_unlock_schedule` · `regulatory_events` |

## Install & daily ops

Not on PyPI yet. After the same env setup as the demo:

```bash
pip install -e ".[tdx]"          # add [dev] for development; or: uv sync --extra tdx
cp configs/ashare-lake.example.toml configs/ashare-lake.toml
asl init   --config configs/ashare-lake.toml    # dirs / manifest / views + first backfill
asl run daily --config configs/ashare-lake.toml # daily incremental
asl status --config configs/ashare-lake.toml
```

Optional extras (`valuation` / `macro` / `nlp` / `structure`):
[installation](docs/getting-started/installation.md).  
After the initial backfill, run the [acceptance checks](docs/operations/runbook.md)
before wiring cron.

## Reading data

```python
from ashare_lake.query import load

bars = load(
    "daily_bars",
    start="2020-01-01", end="2025-12-31",
    adjust="hfq",              # None | "qfq" | "hfq"
    universe="all_a",
)
roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

<p align="center">
  <img src="docs/assets/asl-load.png" alt="Python load(): read daily bars from local curated Parquet" width="720" />
</p>

```bash
asl query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/ashare-lake.toml
```

Or open `{data_root}/duckdb/ashare-lake.duckdb`, or Polars `scan_parquet` on
day-partitioned datasets. For year/month partitions (e.g. `index_bars`), prefer
`asl query` / `load()` so hive labels do not collide with real dates — see
[lake-layout](docs/architecture/lake-layout.md).

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet
  derived/   adj_factors/...
  staging/   raw landing per run (cleanable after compact)
  meta/      manifest, quality findings, watermarks, on-demand cache
  duckdb/    ashare-lake.duckdb
```

## Known limitations

- **Survivorship bias:** delisted names need `asl delisted backfill` + `repair`
  before trusting return series
- **Network:** some HTTP / sector backfills need mainland egress; demo needs TDX
- **No PyPI yet:** source install only

More (historical ST filters, BSE/BJ, partition pitfalls):
[runbook](docs/operations/runbook.md) · [legal](docs/legal-and-data-sources.md).

## Project status

[0.1.0](CHANGELOG.md) — first public release of a data layer the author runs on
a personal daily cron. Schema / `load()` may change before 1.0; see
[CHANGELOG](CHANGELOG.md).

Personal project: issues and PRs welcome, responses best-effort.
[CONTRIBUTING](CONTRIBUTING.md) · [SECURITY](SECURITY.md). Docs are
Chinese-first; [CHANGELOG](CHANGELOG.md) and [ADRs](docs/adr/) stay in English.

## Docs

[docs/README.md](docs/README.md) · [comparison](docs/comparison.md) ·
[installation](docs/getting-started/installation.md) ·
[quickstart](docs/getting-started/quickstart.md) ·
[configuration](docs/getting-started/configuration.md) ·
[architecture](docs/architecture/overview.md) ·
[catalog](docs/datasets/catalog.md) · [schema](docs/datasets/schema.md) ·
[query guide](docs/datasets/query-guide.md) ·
[runbook](docs/operations/runbook.md) · [CLI](docs/reference/cli.md) ·
[Python API](docs/reference/python-api.md)

## License

Code is [MIT](LICENSE). Market data and announcements you land locally remain
subject to upstream terms; this repo ships no data and grants no redistribution
rights — see [legal](docs/legal-and-data-sources.md).
