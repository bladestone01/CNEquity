# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-07-19

First public release of the self-hosted A-share Parquet data layer.

### Added

- Multi-source ingest (TDX/mootdx, EastMoney, Sina, CNINFO, optional Baostock/AkShare)
  into a staged → curated → derived lake layout
- CLI (`asl`) for `init`, `run daily`, `backfill`, `compact`, `derive`, `audit`,
  `status`, `retry`, `query`, `catalog`
- Python `load()` API with `adjust` / `universe` / point-in-time `as_of`
- DuckDB views over curated Parquet
- Dataset coverage across reference, bars, corporate actions, fundamentals,
  capital flow, sector/industry structure, macro, news/sentiment, and risk events
- Quality audit (PK, mock-source guard, adj-factor reconciliation, cross-checks)
- Optional extras: `tdx`, `valuation`, `macro`, `nlp`, `structure`, `dev`
- Ops scripts for daily pipeline, health notify, and meta backup
- Docs: comparison vs AkShare/Tushare/Baostock, legal notes, schema contract,
  per-source limits, runbook

### Security / hygiene

- Ignore runtime logs and local tool/editor dirs
- TLS verify on by default for HTTP clients
- Project URLs point at `rootSunc/ashare-lake`

[0.1.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.1.0
