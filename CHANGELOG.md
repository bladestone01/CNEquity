# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Breaking (data trust):** TDX adapters no longer fall back to fabricated
  mock data on failure. They raise `TdxSourceError` and fail the batch unless
  `[tdx_protocol].allow_mock = true` (tests/demo only); mock rows are labeled
  `source="mock"` and audit reports an error finding when they reach curated.
- **Breaking (schema):** `fetched_at` is now a real UTC timestamp
  (`Datetime("us", "UTC")`) instead of an ISO string, matching
  `docs/schema.md`. `compact` normalizes previously written files on read;
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
