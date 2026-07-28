# A-Share Data Lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/rootSunc/ashare-lake/graph/badge.svg)](https://codecov.io/gh/rootSunc/ashare-lake)
[![PyPI](https://img.shields.io/pypi/v/ashare-lake.svg)](https://pypi.org/project/ashare-lake/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md)

# A self-hosted, daily-refreshable, provenance-tagged financial research base

Multi-source ingest → daily jobs → curated Parquet with row-level provenance.  
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
pip install ashare-lake
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

AkShare / efinance answer “how do I fetch?”; Tushare answers “cloud wide tables”;
Qlib / vn.py answer “research / trading platform”.
**ashare-lake** owns the middle layer: many sources into one contract, as a
resumable, provenance-tagged, auditable local Parquet lake.
Details: [comparison](docs/comparison.md) (Chinese).

| What you care about | **ashare-lake** | AkShare / efinance | Tushare Pro | Baostock / mootdx | Qlib / vn.py |
|--|--|--|--|--|--|
| Local, resumable data base | **Lake + daily jobs** (watermarks / retry / audit) | In-memory fetch; you own orchestration | Cloud credits, not a self-hosted lake | Session fetch, no lake | Tied to platform data subsystem |
| Provenance / auditability | **Row-level provenance** + write-time schema checks | Usually no shared contract | Platform fields | No lake contract | Varies |
| Cross-source validation | **Primary curated + backup snapshots**, diffable, never silent replace | One call, one source | One vendor | One source | Varies |
| Stable research semantics | **`load()` contract**: adjust / universe / PIT `as_of` | DIY | DIY | DIY | Platform semantics |
| When a source fails | **Fail the batch**, surface it, retry by batch | Up to caller | Up to vendor | Up to caller | Varies |
| Standalone research data base? | **Yes** (lake + daily jobs + `load()`) | No — you still build landing/orchestration | Cloud tables, not self-hosted | No — session fetch | Yes, but platform-tied |

One line: **others fetch frames; this ships a reproducible research base.**
Trade-offs (unadjusted storage, no auto-failover, …):
[comparison](docs/comparison.md).

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

```bash
pip install ashare-lake
asl demo                          # writes configs/ashare-lake.demo.toml under cwd
```

Full-market daily ops:

```bash
asl config init                   # writes configs/ashare-lake.toml (workers=1 on macOS)
# edit data.root if needed
asl init   --config configs/ashare-lake.toml    # dirs / manifest / views + first backfill
asl run daily --config configs/ashare-lake.toml # daily incremental
asl status --config configs/ashare-lake.toml
```

No extras — one command installs every source. Dependency breakdown in
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
- **Config for full init:** run `asl config init` (or write your own toml);
  `asl demo` writes its own tiny config

More (historical ST filters, BSE/BJ, partition pitfalls):
[runbook](docs/operations/runbook.md) ·
[troubleshooting](docs/operations/troubleshooting.md) ·
[legal](docs/legal-and-data-sources.md).

> Detailed getting-started docs are Chinese-first; this English README is the
> short path. See [docs/](docs/README.md) for the full index.

## Project status

[0.2.0](CHANGELOG.md) — published on [PyPI](https://pypi.org/project/ashare-lake/);
first public data-layer release the author runs on a personal daily cron.
Schema / `load()` may change before 1.0; see [CHANGELOG](CHANGELOG.md).

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
