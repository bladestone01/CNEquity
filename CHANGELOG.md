# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **`macro_indicators` rows fetched from AkShare were stamped
  `source = "eastmoney"`.** `fetch_macro_indicators` returned rows without a
  `source` column, and `steps/macro_risk.py` passed a blanket
  `source="eastmoney"` to `run_incremental_fetched`; since `with_provenance`
  only fills the column when it is absent, every monthly PMI / M2 / 社融 value
  landed in curated attributed to EastMoney. That is a provenance
  falsification: it defeats ADR-0003's premise that a curated row names the
  feed it came from, and left `audit` with no way to tell the two apart. The
  adapter now stamps `source` per row (`eastmoney` / `akshare`); the step's
  value only applies to the empty-frame case.

- **The monthly macro series ignored `[sources.akshare]`.** `_akshare_rows`
  was called unconditionally from `fetch_macro_indicators`, so setting
  `enabled = false` disabled the ST cross-check (`steps/reference.py` did
  check the flag) but not the macro path — AkShare was still imported and
  called on every daily run. It is now gated like the other call site: an
  absent `[sources.akshare]` section counts as off, as does the no-config
  path. Daily rates (`cnbond_yield_10y`, `shibor_3m`, `lpr_1y`) are unaffected;
  they come from EastMoney directly.

- **`daily_bars.volume` mixed two units in one column, off by exactly 100×.**
  The schema has always documented 股, but only `ths` and `baostock` wrote it:
  `tdx_protocol` passed TDX's native 手 straight through (median
  `amount / close / volume` = 100.000 over 12,182,204 curated rows), and the
  Sina adapter actively divided by 100 on the mistaken belief that the lake
  stored 手. Every turnover and liquidity factor built on the column was wrong
  by 100× for whichever rows it happened to touch.

  Every adapter now normalizes to **股** at its own boundary — TDX daily and
  both EastMoney paths multiply by 100, Sina no longer divides, `ths` and
  `baostock` are unchanged and documented as already correct. The per-vendor
  units and their evidence live in `ashare_lake.domain.units`.

  EastMoney's 手 reading is inferred, not independently verified: `push2his` is
  unreachable from many networks and the only EastMoney rows in the lake are
  all-zero suspension placeholders. It follows the same endpoint and field that
  `commodity_bars` already documents as 东财口径, and the new check below
  catches it if it is wrong.

### Added

- `daily_bars_volume_unit` audit check (`quality/unit_checks.py`): flags, per
  source, any median `amount / close / volume` outside [0.8, 1.25], so an
  adapter that stops converting cannot silently reintroduce the break. Runs as
  part of `asl audit` / `lake_health`.

### Changed

- **`daily_bars` is now `data_version = v2`** — v2 guarantees `volume` is 股;
  v1 means the unit depends on `source`. `data_version` is resolved per dataset
  via `domain.schemas.data_version_for`; every other dataset stays on v1.

  Rows already curated are wrong under either convention and need a one-off
  rewrite:

  ```bash
  scripts/migrate_daily_bars_volume_v2.py --config configs/ashare-lake.toml --dry-run
  scripts/migrate_daily_bars_volume_v2.py --config configs/ashare-lake.toml --apply
  ```

  It rescales `tdx_protocol` / `sina` v1 rows by 100, leaves the rest, stamps
  everything v2, and is idempotent. `fetched_at` is deliberately not restamped.
  Back up before `--apply`; it edits curated in place.

- `index_bars` and `sector_bars` keep TDX's own volume unit and stay on v1.
  It does not reconcile against the constituent sum at any power of 100, so it
  is not silently rescaled to match; see `docs/datasets/schema.md`.

## [0.3.1] — 2026-07-29

### Changed

- Lowered the supported Python floor from 3.11 to **3.10** (`requires-python = ">=3.10"`).
  EastMoney compact `YYYYMMDD` kline dates now parse via `strptime` (3.10
  `date.fromisoformat` only accepts dashed ISO forms). CI / classifiers cover
  **3.10–3.13**.
- README architecture diagram is a single bilingual JPG
  (`docs/assets/architecture-overview.jpg`); the Pillow renderer and separate
  zh/en PNGs are gone.

### Fixed

- `asl config init` always writes an **absolute** `data.root` (resolving the
  template's `./data/ashare-lake` against the current working directory) so
  `asl doctor` is green on the default first-run path.
- Default `[on_demand].datasets` is only `stock_news` and `research_reports`.
  `announcement_body` / `financial_reports` raise `NotImplementedError` instead
  of caching empty placeholder JSON. Failed research_reports fetches are not
  cached either.
- ImportError hints for baostock / pandas no longer recommend removed extras
  (`[valuation]` / `[structure]`); they point at reinstalling `ashare-lake`.

## [0.3.0] — 2026-07-29

### Upgrading from 0.2.x

Neither pip nor uv removes a package that merely stopped being a dependency, so
an upgraded environment keeps `mootdx` and its `py-mini-racer`. The latter then
shares the `py_mini_racer` import package with the `mini-racer` that AkShare
brings in, and one silently overwrites the other. Nothing this project fetches
is affected — none of the AkShare endpoints it calls evaluate JS — but AkShare's
own cninfo and sina APIs would break if you call them directly.

    asl doctor        # reports it
    asl doctor --fix  # resolves it

`mootdx` itself is left behind as dead weight and can be uninstalled. A fresh
environment has none of this.

### Changed

- `pip install ashare-lake` is the whole install. Every runtime source — AkShare,
  Baostock, SnowNLP, and the pandas/openpyxl/xlrd trio that parses the Shenwan
  and CNI constituent spreadsheets — is a hard dependency, so no daily or
  backfill step can silently lose a source because an extra was forgotten. Costs
  roughly 217MB over the previous minimal install.
- TDX quotes now use a vendored wire client (`adapters/tdx_protocol/_wire`,
  derived from tdxpy, MIT) instead of `mootdx`. Both `mootdx` and `tdxpy` were
  last released in 2024 and are unmaintained. Verified byte-identical to the
  previous implementation against live servers, including the full 51478-row
  security list.
- `httpx` is no longer capped at `<0.26`; that ceiling came from `mootdx`.
  Installs now resolve to 0.28.x.
- The bundled fallback TDX host list is now maintained in-tree
  (`adapters/tdx_protocol/hosts.py`). A probe of all 49 known hosts found every
  one of mootdx's 38 dead; the four that serve real bars are ordered first.
  Server selection went from failing across 16 probes to resolving in ~3s.

### Added

- Native Windows 10/11 (64-bit) support: cross-platform file locks replace
  Unix-only `fcntl.flock` in run locks, watermark writes, rate limiting, and
  staging cleanup (`ashare_lake.file_lock`).
- CI `windows-latest` job running the offline unit suite.
- `asl config init` defaults `workers = 1` on Windows (same as macOS); raising
  workers later is allowed — Windows uses spawn, not the unsafe macOS fork path.
- Installation docs cover PowerShell / cmd, path forms, and the supported
  Windows scope (x86-64; 32-bit / ARM64 deferred).
- `asl doctor` — checks what `asl config validate` deliberately cannot, since
  that command is environment-blind: whether `data.root` is absolute, present and
  writable, and whether every declared dependency actually imports. `--fix`
  repairs the `py_mini_racer` distribution collision cross-platform.
- Guards (`tests/unit/test_tdx_decoupling.py`) that fail the build if `mootdx`,
  `tdxpy` or the racer packages are imported or re-declared as dependencies.
- README (zh/en) leads with a **shortest path to data** (demo vs daily lake),
  then datasets and the peer comparison table — less duplicate install/read
  sections for first-time readers.
- README shows a layered architecture PNG (zh/en) above the shortest path;
  drops the one-line peer punchline under the comparison table.
- README screenshots re-rendered without the retired `mootdx` probe line;
  banner copy tracks current `asl demo`.
- Docs hub reordered (install/quickstart first); `architecture.md` and
  `datasets/README.md` folded into overview/catalog stubs; module
  cli/query/config pages reduced to source maps.
- Architecture PNGs list primary + supplement adapters (`ths` / `sw` / `cni` /
  `macro`, plus calendar seeds note).

### Fixed

- `bars()` now honours the market derived from the exchange suffix. `mootdx`
  had no such parameter, so the value this project computed was silently
  discarded and re-derived from the code prefix.
- `asl demo` no longer appears to hang. It left its probe client open, and the
  heartbeat thread is not a daemon, so the interpreter stayed alive after all
  six steps had already printed. The client is closed now.
- The vendored client grew back `do_heartbeat`, which the trim to five methods
  had dropped. `HeartBeatThread` calls it by name every 10s, so every keepalive
  raised AttributeError — invisible to any test short enough not to reach the
  first interval.
- DuckDB view globs now use POSIX paths (`as_posix()`), so Windows backslashes
  no longer break `read_parquet(...)` SQL literals.
- Polars recursive scans go through `parquet_glob()` (same POSIX rule); the
  instruments planner uses `Path.rglob` instead of `glob.glob(f"{Path}/…")`.
- `asl config init --data-root` no longer strips escaped backslashes when the
  path is a Windows `C:\…` form (callable `re.sub` replacement).
- `asl demo` writes a TOML-safe `data.root` (escaped POSIX path) so follow-up
  `asl query --config configs/ashare-lake.demo.toml` works on Windows.
- `asl doctor` probes writability with a real create/delete (not `os.access`) and
  suggests an ACL fix on Windows instead of `chmod`.
- EastMoney sticky IP / CLI sticky reads always use UTF-8.
- Atomic parquet replace retries briefly on `PermissionError` (WinError 32 when
  DuckDB / Explorer still holds the destination).
- TDX heartbeat thread is daemon and is joined on disconnect, so spawn workers
  do not linger after close.
- Test helpers embed `data.root` via `path_for_toml()` so Windows CI no longer
  dies on `TOMLDecodeError: Invalid hex value` from unescaped `C:\Users\…`.
- Windows CI: `path_for_toml(Path("/tmp/…"))` assertion accepts drive-letter
  POSIX forms (`D:/tmp/…`) on `win32`.
- Offline unit coverage expanded across EastMoney / cninfo / failover /
  sentiment / sector helpers so project branch coverage sits above 80%.
- Stale docs: removed retired pip extras (`[macro]` / `[valuation]` / …);
  clarified `ASL_*` env vars are script-only (`asl` CLI does not read them);
  fixed eastmoney adapter CLI relative link and quickstart Init anchor.

### Removed

- All extras (`tdx`, `macro`, `nlp`, `valuation`, `structure`, `all`).
  `pip install "ashare-lake[tdx]"` from an older doc still installs correctly:
  pip warns that the extra is not provided and continues, uv says nothing.
- Contributor tooling moved from the `dev` extra to a PEP 735 dependency group:
  `pip install -e . --group dev` (pip >= 25.1) or `uv sync`.

## [0.2.0] — 2026-07-27

### Added

- `asl config init` writes the packaged example TOML (no repo checkout needed);
  forces `orchestrator.workers = 1` on macOS
- Packaged template at `ashare_lake.config.templates` (kept in sync with
  `configs/ashare-lake.example.toml`)

### Fixed

- PyPI project page: ship a short Chinese `README.pypi.md` with absolute GitHub
  links (full `README.md` relative paths break on pypi.org)

### Changed

- Document `pip install "ashare-lake[tdx]"` as the primary install path
- `pyproject.toml` `readme` points at `README.pypi.md` instead of `README.md`
- Getting-started docs use `asl config init` instead of `git clone` + `cp`;
  quickstart separates one-minute demo from full-market init

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

[0.3.1]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.3.1
[0.3.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.3.0
[0.2.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.2.0
[0.1.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.1.0
