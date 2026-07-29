# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Lowered the supported Python floor from 3.11 to **3.10** (`requires-python = ">=3.10"`).
  EastMoney compact `YYYYMMDD` kline dates now parse via `strptime` (3.10
  `date.fromisoformat` only accepts dashed ISO forms).

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

[0.3.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.3.0
[0.2.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.2.0
[0.1.0]: https://github.com/rootSunc/ashare-lake/releases/tag/v0.1.0
