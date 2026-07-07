# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **CDR handling (689xxx segment)** — classify CDRs and exclude them from `all_a`
  - `is_cdr_symbol` in `domain/symbols`; tdx instruments adapter emits
    `asset_type="cdr"`; the `all_a` universe excludes SH 689xxx (depositary
    receipts, no sina/tdx-xdxr factor coverage — 689009.SH has real dividends,
    so `factor=1.0` would be wrong). `derive_adj_factors` skips CDR symbols
    instead of emitting a fetch-failure finding each run. CDR bars stay in the
    lake; direct `symbols=` loads report `adj_is_exact=False`. Lake patched:
    689009.SH `asset_type=cdr`, `list_date=2020-10-29`. Fixes strict_adj
    `ReaderError` in downstream `load(..., universe="all_a", strict_adj=True)`.
- **R-19 — TDX daily bars pagination early stop**
  - Stop paging when the oldest date in a page is before the requested start window.
- **R-22 — fail-loud EastMoney/CNINFO pagination**
  - Datacenter helper retries then raises; corporate_actions backfill paginates fully;
    CNINFO announcement fetch raises on mid-pagination failure.
- **R-24 — atomic curated/derived parquet writes** via temp file + `os.replace`.
- **R-26 — config/CLI fixes**
  - Loader reads `[job.init.phases].names`; CLI defaults to `configs/stockdata.toml`;
    `sde compact` runs full `step_compact`; `sde backfill` compacts on success.
- **R-21 — EastMoney cross-process rate limit (partial)**
  - `EastMoneyClient` accepts `config` and uses `config.rate_limit("eastmoney")` when set.
- **R-20 — adj_factors fail-loud (partial)**
  - Raise when fetch fails with no cache; append-only/qfq rewrite deferred to P2.
- **R-25 — watermark scan (partial)**
  - Derive max partition date from hive directory names before falling back to full read.
- **R-16 — instruments merge compact and list/delist dates**
  - Compact merges with existing curated rows; symbols missing from TDX fetch are
    retained with `delist_date` inferred. EastMoney clist (`f26`) enriches `list_date`.
- **R-18 — compact gate and watermark protection**
  - Skip compact/watermark advance for datasets with failed batches in the run;
    audit emits `compact_skipped` warnings. `sde retry` runs compact when all
    batches succeed.
- **R-17 — corporate_actions daily canonical source**
  - Daily incremental uses EastMoney ex-date API (`source=eastmoney`); backfill
    uses TDX xdxr per symbol (`source=tdx_protocol`). TDX snapshot on ex-date
    symbols for cross-audit when failover enabled. ADR-0003 amended for
    per-dataset/mode canonical source.
- **R-15 / R-23 — schedule groups land data in curated**
  - Finalize steps defer until all fetch steps in the same run complete.
  - `audit` depends on `compact` + `derive_adj_factors`.
  - All schedule groups append `compact`; compact only merges datasets staged in the
    current `run_id`.

### Changed
- **PRD v2.2 — 2026-07-06 全库架构评审**：状态标注与代码全面同步（M2–M4/v1.1
  已实现项从 🔴 更正为 🟢/🟡）；新增风险 R-15–R-26（分组运行不落 curated、
  audit 先于 compact 执行、corporate_actions daily 路径失效、部分失败仍推水位、
  instruments 覆盖丢退市股、TDX 分页无早停、分页静默截断等）；新增 §11.1
  v1.2 修复计划（P0/P1/P2）。README 同步标注已知缺陷与 Phase 5 修复批次。

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
