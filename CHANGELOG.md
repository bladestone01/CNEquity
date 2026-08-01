# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **A single reconnect failure could take down an entire full-market intraday
  sweep.** `fetch_minute_bars` opens a fresh TDX connection per 50-symbol
  batch; run against the full market (7,747 symbols, ~155 reconnects), one
  connect attempt hit a `socket.recv` timeout ~44 minutes in and the exception
  propagated all the way out of the step, discarding every batch already
  fetched (staged but never compacted, since compact only runs on a
  success/warning step). A second full-market attempt got through the fetch
  cleanly but returned zero rows for all 7,747 symbols — a TDX host degrading
  under sustained connection churn rather than raising outright.

  Fixed two ways. `client.py` now retries a failed connect once, against a
  freshly re-probed server, before giving up (`_connect_with_retry`) — the
  same "rotate server, don't hammer the dead one" pattern `fetch_index_bars`
  already used. `steps/intraday.py` now catches a whole batch failing outright
  and records its symbols as failed rather than letting the exception abort
  the step — the same contract a single symbol's failure already had, just at
  the batch grain. The batch size (`_BATCH_SYMBOLS`) also moved from 50 to 200,
  cutting full-market reconnects roughly 4x, while staying small enough that a
  killed run still loses minutes, not hours.

- **No-trade bars stored a denormal turnover instead of zero.** TDX's packed-
  float decoder maps a raw zero quantity to `2**-127` (~5.9e-39) rather than
  `0.0`, so every suspended day landed in curated with that much `amount`
  against `volume = 0` — 439,774 rows in the reference lake, contradicting the
  documented suspension convention (`volume=0`, `amount=0`) and quietly making
  `amount > 0` mean "was quoted" rather than "traded". `volume` escaped it
  through `int()` truncation; `amount` is a float and kept it.

  Fixed at the adapter boundary for both the daily and intraday paths
  (`adapters/tdx_protocol/_decode.py`). Rows already written are cleaned by the
  same `scripts/migrate_daily_bars_volume_v2.py` pass that does the v1→v2
  volume rewrite. It matters more intraday, where an illiquid name has dozens
  of no-trade minutes a day and a halted one has a full session of them.

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

- **`minute_bars` dataset — intraday (1m) bars, opt-in.** Registered with a
  schema, a primary key of `(symbol, trade_date, bar_time, frequency)`, day
  partitions, a step (`steps/intraday.py`, `group="intraday"`), `load()`
  support including `adjust="qfq"/"hfq"`, and four audit checks. Off by default
  (`[minute_bars].enabled = false`) and never on the daily waves: full-market
  1m is ~1.3M rows and ~35MB a day (8.4GB a year, against 468MB for the entire
  daily lake 2001–2026), which must not become what `asl init` costs someone
  who never asked for it. `[minute_bars].scope` defaults to `index:000300.SH`
  (~300 names, ~2MB a day).

  `bar_time` is the bar's **closing** minute, as TDX labels them: a full
  session is 240 bars over 09:31–11:30 and 13:01–15:00, and the 15:00 bar
  carries the closing auction. Prices are unadjusted, like the daily bars;
  adjustment joins the day's factor at query time.

- **`minute_bars_5m` — 5-minute bars, the only intraday frequency with real
  history.** A separate dataset rather than a `frequency` value inside
  `minute_bars`, because the source keeps 491 trading days of 5m against 95 of
  1m, and a dataset carries one watermark, one `coverage_start` and one
  horizon — holding both frequencies would make all three wrong for both. The
  dataset↔frequency mapping is `DatasetSpec.intraday_frequency`, and the steps,
  the audit checks and `load()`'s adjustable set are all derived from it, so
  adding a frequency is one registry entry.

  Configured together: `[minute_bars].frequencies = ["1m", "5m"]` shares one
  scope across both. At a fifth of 1m's row rate it is ~6MB a day at full
  market (1.5GB a year, against 8.4GB for 1m).

  15m/30m/60m are deliberately **not** stored: they aggregate exactly from 5m
  (48 bars divide by 3, 6 and 12 onto identical closing-minute boundaries —
  verified against live data), so three more datasets would hold a
  `group_by_dynamic` away from data already present. `docs/datasets/catalog.md`
  carries the resampling snippet.

- `asl demo --intraday`: adds a seventh step capturing 1m bars for the same
  handful of symbols and printing a real session, so the closing-minute
  labelling and the 240-bar shape are visible without building a lake.

- `[minute_bars].fetch_workers`: concurrent TDX connections for the intraday
  fetch, one per thread. It does **not** raise the request rate — the limiter
  is cross-process and paces every request either way — it only stops one lane
  idling on network latency. Measured over 40 symbols × 5 sessions: 4.50 req/s
  at 1 worker against 10.13 at 4, which is the ceiling the 100ms limiter
  already permits. Threads rather than the daily path's ProcessPool, so it
  works on macOS too, where the fork-unsafe wire client pins `workers` to 1.

  Left at 1 by default: the measurement is a two-minute burst, not a five-hour
  sweep. `docs/operations/runbook.md` carries the disk and wall-clock numbers
  for a full-market decision.

- **`DatasetSpec.history_horizon_days` — how far back a source still serves.**
  Measured 2026-08-01, TDX keeps 22,800 1-minute bars per symbol and 23,568
  5-minute. The cap is a **bar count**, not a date: divided by a full session
  that is 95 and 491 trading days, which holds for any instrument quoted every
  session — every A-share stock, and what these datasets are for. An instrument
  with bars on only scattered days reaches proportionally further back
  (162107.SZ, a barely-traded LOF, holds 3,216 5m bars over 67 days and so
  reaches 2012), so the field is the guarantee for a normal stock rather than a
  hard ceiling for every symbol.

  An older window returns *nothing*, not less, and no backfill source extends
  it — `history_mode = by_date` alone would have promised a decade. Surfaced in
  `list_datasets()`, and `asl backfill` now refuses a `--start` before the
  horizon instead of sweeping for hours into an empty lake.

- `DatasetSpec.backfill_chunk_days`: backfills for datasets that declare it run
  as a sequence of compacted date slices. `compact` reads a whole run's staging
  into one frame, which a 95-day full-market `minute_bars` seed (~123M rows)
  would not survive; slicing also makes a killed sweep resumable at the last
  compacted slice rather than losing everything.

- **A single intraday bar's volume is not reproducible; the day's total is.**
  Fetching the same settled window twice returns different `volume`/`amount`
  for ~0.6% of bars (257 of 43,920 over 40 symbols × 5 sessions). It is
  boundary attribution, not corruption: a trade sitting on a minute edge lands
  either side depending on when the server aggregated, and the neighbour
  compensates exactly — across all 183 symbol-days in that sample the daily
  volume totals were identical and the amount totals matched to 0.00e+00
  relative. Documented in `docs/datasets/schema.md`, because a factor that
  reads absolute per-bar quantities needs to know.

  Unrelated to concurrency: two *serial* fetches disagreed on more rows (435)
  than a serial and a threaded one did (181).

- Intraday bars outside continuous trading are dropped at parse time. The
  source really does emit them: 162107.SZ, a barely-traded LOF, returns a
  13:00-labelled bar on days it did not trade, zero volume with a stale close
  carried forward — padding, not a tradable minute (an active name emits none;
  600519 over 2,400 bars, zero). Keeping them would put a phantom bar in every
  gap check and skew any resampling that assumes fixed bar counts.

- `asl backfill <intraday dataset> --symbols A,B`: restrict a one-off intraday
  backfill without editing the config — it overrides `[minute_bars].scope` for
  that run and enables capture, so pulling a few names does not mean flipping
  config flags first. Rejected for non-intraday datasets, which take their
  universe from `instruments`.

- Intraday audit checks (`quality/intraday_checks.py`): `minute_bars_off_session`
  and `minute_bars_trade_date_mismatch` (error), `minute_bars_session_coverage`
  and `minute_bars_daily_reconciliation` (warning). The reconciliation compares
  both volume and turnover against `daily_bars`: `volume` is the column with a
  unit history and so catches a conversion slip, while `amount` is yuan from
  every source and so cannot be wrong for a unit reason — a break there means
  the wrong bars, not the wrong scale. A session that quietly loses
  40 of its 240 bars still has rows on every trading day and passes every
  dataset-level check the lake already runs; the daily reconciliation is the
  only one that compares the series against independently fetched data.

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
