# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Phase 4 (M4) — multi-source snapshots and cross-source audit**
  - `SnapshotStore` writes backup captures to `meta/source_snapshots/` (ADR-0003).
  - Failover on `daily_bars` batch failure (EastMoney kline backup); `corporate_actions`
    EastMoney snapshot each run when `[failover]` enabled.
  - `quality/source_diff.py` compares curated primary vs snapshots; `audit` writes
    `meta/quality/source_diffs/{run_id}.json` with ±10bps price drift checks.
  - Config `[failover]` with per-dataset primary/backup/compare_fields.
  - Tests: `tests/unit/test_phase4_failover.py`.
- **stock_news on-demand + NLP sentiment chain**
  - EastMoney `np-anotice-stock` adapter with per-headline NLP scoring.
  - `OnDemandService` real fetch + cache; `sde query --dataset stock_news --symbol`.
  - Batch `sentiment_scores` adds `stock_news_nlp` channel (news for announcement
    symbols + top turnover); warms on-demand cache.
  - `domain/sentiment.py` keyword lexicon + optional SnowNLP (`pip install -e ".[nlp]"`).
  - Config `[sentiment]` (`use_snownlp`, `news_symbol_limit`); tests added.
- **v1.1 P2 — research / sentiment batch**
  - `institutional_holdings` + EastMoney `RPT_MAIN_ORGHOLD` (NOTICE_DATE incremental).
  - `analyst_consensus` + EastMoney `RPTA_WEB_RES_PROFIT`.
  - `sentiment_scores` derived from `announcement_index` keyword lexicon v1.
  - Schedule group `research` at 18:30; schemas, compact, `load()` wired.
  - Tests: `tests/unit/test_v11_p2.py`; PRD appendix A column defs.
  - Optional extra `[macro]` (`akshare`) enabled in example config for PMI/M2/社融.
- **v1.1 — macro / risk batch (P1 + regulatory P2)**
  - `macro_indicators` step + EastMoney datacenter adapter (`shibor_3m`, `cnbond_yield_10y`,
    `lpr_1y`; optional akshare for PMI/M2/社融 when installed).
  - `market_breadth` derived from curated `daily_bars` (advance/decline/limit counts).
  - `share_unlock_schedule` + EastMoney `RPTA_WEB_XSJJMX` adapter.
  - `regulatory_events` + CNINFO keyword filter adapter.
  - Schedule group `macro_risk` at 18:00; schemas, compact, `load()` wired.
  - Tests: `tests/unit/test_v11_macro_risk.py`; PRD appendix A column defs.
- **Phase 3+ (M3+) — fundamentals & structure batch**
  - `financial_statement_items` step + EastMoney `RPT_LICO_FN_CPD` adapter (PIT via
    `announce_date`; empty trading days allowed).
  - `index_constituents` + `industry_members` schemas, adapters, steps.
  - Schedule group `fundamentals` at 17:30; `load()` + compact wired.
  - Tests: `tests/unit/test_m3plus.py`; PRD appendix A column defs.
- **Phase 3 (M3) — batch 数据集 §4.2**
  - Schemas + PKs for `fund_flow`, `margin_trading`, `northbound_holdings`,
    `northbound_flows`, `valuation_metrics`, `sector_members`, `announcement_index`,
    `dragon_tiger`, `block_trades`.
  - EastMoney adapters (`adapters/eastmoney/capital.py`, `valuation.py`, `sectors.py`)
    and CNINFO `announcement_index` adapter.
  - Steps: `steps/capital.py`, `fundamentals.py`, `structure.py`; `announcement_index`
    in `events.py`.
  - `compact` / `load()` / config schedule groups `capital` + `signals` wired.
  - PRD appendix A column definitions for all M3 datasets.
  - Unit tests: `tests/unit/test_m3_steps.py`, `test_m3_adapters.py`.
- **Phase 2 — Python read API (`query/reader.py`)**
  - `load(dataset, start, end, adjust, universe, as_of, items, symbols)` with Polars backend.
  - Built-in qfq/hfq adjustment (joins `adj_factors`, emits `adj_open` … `adj_close`).
  - Universe filter `all_a` (instruments list/delist dates + trading_status ST/suspended).
  - PIT queries for `financial_statement_items` via `announce_date <= as_of`.
  - `financial_statement_items` schema + PK registered in `domain/schemas.py`; PRD appendix A
    documents the PIT contract.
  - Unit tests in `tests/unit/test_reader.py`.
- **Phase 1 (M2) — P0 真实化 + 稳定日更**
  - `trading_calendar`: bundled exchange seed CSV (2016–2027) + index-bars fallback
    (`adapters/calendar/`).
  - `corporate_actions`: mootdx xdxr primary + EastMoney datacenter backup; same-day
    ex_date drives `symbols_to_rebackfill`.
  - `daily_bars`: TDX pagination (`offset`/`start` in 800-bar pages) for full backfill
    to 2016.
  - Incremental watermarks in `meta/state/{dataset}.json`; daily runs resume from
    last-success trade_date.
  - Symbol-batch manifest entries from worker pool; `sde retry` re-runs only failed
    batches.
  - `adj_factors`: parallel Sina fetch (ThreadPoolExecutor) + per-symbol cache under
    `meta/adj_factors_cache/`; refresh on ex-date symbols only.
  - `trading_status`: EastMoney ST board + suspension datacenter API.
  - Unit tests for calendar, state, bars pagination, and batch-level retry.

### Changed
- **Documentation consolidation:** `docs/schema.md`, `docs/datasets.md`, and
  `docs/operations.md` merged into `docs/PRD.md` (v2.1) as appendices A/B/C —
  one requirements document as the single source of truth. README rewritten
  in Chinese as an end-state usage guide (data layers, three consumption
  paths, data-trust principles) with an actionable phased roadmap.
- **Package reorganization** (no behavior change, import paths updated):
  - `duckdb/` → `query/` (consumption layer: views, on-demand, future read
    API; also stops shadowing the third-party `duckdb` module name).
  - `catalog/init_layout.py` → `storage/layout.py`; `catalog/on_demand.py` →
    `query/on_demand.py`; `catalog/` removed.
  - `workers/pool.py` → `orchestrator/worker_pool.py`; `workers/` removed.
  - `adapters/rate_limit.py` → `adapters/throttle.py` (avoids duplicate module
    name with `domain/rate_limit.py`).
  - `steps/builtin.py` split by PRD data layer: `reference.py` (L0),
    `bars.py` (L1), `events.py` (L2), `finalize.py`, shared helpers in
    `common.py`; importing `stock_data_engine.steps` registers everything.
- **Breaking (data trust):** TDX adapters no longer fall back to fabricated
  mock data on failure. They raise `TdxSourceError` and fail the batch unless
  `[tdx_protocol].allow_mock = true` (tests/demo only); mock rows are labeled
  `source="mock"` and audit reports an error finding when they reach curated.
- **Breaking (schema):** `fetched_at` is now a real UTC timestamp
  (`Datetime("us", "UTC")`) instead of an ISO string, matching the schema
  contract (now `docs/PRD.md` appendix A). `compact` normalizes previously
  written files on read;
  regenerating the lake (`sde init` + backfill) is recommended.
- Manifest SQLite connections enable WAL and `busy_timeout` in preparation
  for batch-level writes from worker processes.
- PRD §5.2/§6/§10/§11 status markers synced with code: R-01/02/04/09/10/11
  are implemented (M1 complete); new risk R-14 (mock poisoning) recorded as
  fixed.

### Added
- Project scaffolding adjusted to Python best-practice layout (src layout,
  `tests/{unit,integration}`, `docs/adr/`, CI workflow, tooling config).
- `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`.
- `py.typed` marker (PEP 561) and `python -m stock_data_engine` entry point.

### Notes
- PRD v2.0 documents the target design; implementation is in progress.
  See `docs/PRD.md` status markers (🟢/🟡/🔴) and the roadmap in this repo's
  follow-up implementation plan.

## [0.1.0] - 2026-06-28

### Added
- Initial orchestrator skeleton: Step Registry, Wave engine, SQLite manifest.
- MVP-P0 datasets (skeleton/mock for several sources).
- Parquet staging → compact → curated pipeline with provenance columns.
- DuckDB views, on-demand service, quality audit skeleton.
- CLI: `init`, `config validate`, `servers test`, `run daily`, `backfill`,
  `compact`, `derive`, `audit`, `status`, `retry`, `catalog`, `query`.
