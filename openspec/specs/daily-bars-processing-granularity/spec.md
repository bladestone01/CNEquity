## ADDED Requirements

### Requirement: Processing granularity is selectable via system config

A `daily_bars_granularity` configuration value SHALL select how `daily_bars` batches are fetched, staged, failed, and retried, with exactly two values:

- `symbol` (default): symbol-level attribution — partially successful symbols are staged immediately, only genuinely missing symbol×date keys are recorded as failed and routed to failover / retry;
- `batch`: legacy strict all-or-nothing — any symbol exception or coverage gap fails the whole batch, no rows of that batch are staged, and retry refetches the whole batch.

The system SHALL read this value at run start and apply it consistently to the fetch path, the staging path, the failover trigger set, and the retry scope. An invalid value SHALL be rejected by config validation before any run starts.

The configuration file SHALL be the sole source of this value. `cne run daily` and `cne backfill` SHALL NOT expose a granularity command-line option, and `cne retry` SHALL have no granularity override entry either; the effective granularity for every command is `[orchestrator].daily_bars_granularity` at `load_config` time (for new runs) or the run-recorded value (for retries).

#### Scenario: symbol mode is the default
- **WHEN** no `daily_bars_granularity` is configured
- **THEN** daily_bars runs in `symbol` mode: a 100-symbol batch where 99 return rows and 1 has a genuine gap stages the 99 and fails only the gap key

#### Scenario: batch mode is configured
- **WHEN** `daily_bars_granularity = "batch"` is set
- **THEN** a 100-symbol batch where 1 symbol raises or has no rows fails as a whole: nothing is staged and all 100 symbols are routed to failover/retry, preserving current behavior exactly

#### Scenario: invalid granularity is rejected
- **WHEN** `daily_bars_granularity` is set to an unrecognized value (e.g. `"row"`)
- **THEN** `validate_config` rejects the configuration and the run refuses to start

#### Scenario: no command-line escape hatch exists
- **WHEN** an operator inspects `cne run daily --help` or `cne backfill --help` or `cne retry --help`
- **THEN** no `--granularity` option is present, and the effective mode is always the configured value (new runs) or the run-recorded value (retries)

#### Scenario: a configuration change takes effect on the next run
- **WHEN** an operator edits `daily_bars_granularity` from `"symbol"` to `"batch"` and then runs `cne run daily`
- **THEN** that run executes in `batch` mode, and no ad-hoc per-invocation override is available to change it for one run only

### Requirement: Rerun / retry follows the run's active granularity

A rerun (e.g. `cne retry --run-id`) SHALL apply the granularity that was in effect when the run started, recorded with the run, so historical runs are retried under the same semantics regardless of the current configuration.

- In `symbol` mode, retry SHALL refetch only the recorded failure scope (symbol×date keys), writing to a distinct attempt-level batch file so previously staged partial rows are never overwritten, and only the current attempt is superseded on success.
- In `batch` mode, retry SHALL refetch the full symbol list of the failed batch, exactly as today.

#### Scenario: symbol-mode retry is scoped and non-destructive
- **WHEN** a `symbol`-mode run left a batch failed with failure scope `{s100: [2026-08-20, 2026-08-21]}` and 99 symbols already staged under `part-...-batch-0` file
- **THEN** the retry fetches only `s100`, writes its rows under a distinct attempt-level file, and the earlier staged 99 symbols' rows remain intact in staging and are compacted together

#### Scenario: batch-mode retry refetches the whole batch
- **WHEN** a `batch`-mode run left a 100-symbol batch failed
- **THEN** the retry refetches all 100 symbols and no attempt-level file separation is used

### Requirement: A batch whose failure scope is unresolved stays out of curated

In either granularity mode, compaction SHALL be withheld for a dataset while any of its batches has an unresolved failure scope. Partial `symbol`-mode staging is therefore promoted only after the scope is cleared (via retry), never while a gap remains — the same compact gate semantics as today (status not in success/superseded blocks the dataset).

#### Scenario: partial staging is not promoted with a hole
- **WHEN** a dataset has a `symbol`-mode batch with 99 symbols staged but an unresolved failure scope
- **THEN** `compact` skips that dataset even though 99 symbols' rows are present in staging

#### Scenario: scope cleared then compacted
- **WHEN** the retry resolves the failure scope and the batch reaches `success`
- **THEN** all attempt-level files under the run are compacted into curated together and the dataset watermark may advance

### Requirement: Whole-window suspension exemption uses persisted evidence

A requested stock that has no bars for the whole window SHALL be exempted (not failed, no failover re-fetch) when persisted evidence proves a full-window suspension. Two evidence sources, both read from already-persisted data:

- the `trading_status` dataset marks the symbol `suspended` (or `st` / `*st`) across the window, or
- `daily_bars` shows a run of at least `_ORPHAN_ACTIVE_PLACEHOLDER_MIN_ROWS` (20) consecutive all-zero placeholder rows (flat OHLC, `volume=0`) after the last positive-volume trade inside the window — the same "still tracked as listed ⇒ halted, not delisted" heuristic the delisted path already uses (`delisted.py::_ORPHAN_ACTIVE_PLACEHOLDER_MIN_ROWS`).

A symbol that the persisted evidence leaves unproven SHALL remain a strict gap: reported failed and routed through the existing failover/retry. The exemption classification SHALL never depend on data that only a live vendor call can supply at decision time.

#### Scenario: suspension proven by a placeholder run
- **WHEN** a symbol's `daily_bars` shows ≥ the placeholder-run threshold of all-zero rows after its last positive-volume trade within the window
- **THEN** the symbol is exempted as fully suspended (finding only), no failover is triggered

#### Scenario: suspension proven by trading_status
- **WHEN** the persisted `trading_status` dataset marks the symbol suspended across the whole window
- **THEN** the symbol is exempted (finding only), no failover is triggered

#### Scenario: suspension not proven stays strict
- **WHEN** a listed, non-exempt stock returns no bars in a window and neither `trading_status` nor a placeholder run proves a suspension
- **THEN** the symbol is reported failed and routed through the existing failover/retry

### Requirement: First-trading-day (IPO) no-quote exemption is persisted-evidence-only

A stock listed on the window's latest session (its first trading day) SHALL be exempted when it has no staged bars yet, provided persisted `instruments.list_date` places the listing at the window boundary (`list_date == window end`) and no positive-volume bar exists for it. This covers "listed but the first print has not landed" without treating an ordinary interior gap as a first-day case.

The exemption SHALL be claimed only from persisted evidence (`curated instruments` + absence of the symbol's `daily_bars` rows); it SHALL NOT be resolved by a live vendor probe at decision time. When `list_date` is null/NaT — listing cannot be proven — the symbol SHALL NOT be exempted and remains a strict gap.

#### Scenario: listing date equals the window end
- **WHEN** an instrument's `list_date` equals the window end and no positive-volume bar is staged for it
- **THEN** the symbol is exempted as not-yet-traded IPO (finding only), no failover is triggered

#### Scenario: interior gap is not a first-day case
- **WHEN** an instrument's `list_date` is strictly before the window end, or is within the window but the hole is interior
- **THEN** the symbol is NOT exempted; it is reported failed and routed through failover/retry like any genuine gap

#### Scenario: list_date not authoritative
- **WHEN** an instrument's `list_date` is null/NaT and no positive-volume bar exists
- **THEN** the symbol is NOT exempted (conservative), reported failed, and routed to failover/retry

### Requirement: Exemption classification reads persisted evidence only (constraint)

All exemption classifiers for `daily_bars` symbol-mode failure attribution — not-yet-listed (`list_date > end`), already-delisted (`delist_date < start`), whole-window suspension, and first-trading-day no-quote — SHALL read only persisted evidence: curated/`staging` `instruments` (`list_date`/`delist_date`), curated `trading_status`, curated `daily_bars` rows/placeholders, the delisted catalogue, the delisted identity evidence, and recovery receipts.

These classifiers SHALL NOT invoke live vendor interfaces (TDX, EastMoney, Sina, baostock) at decision time. Rationale: exemptions exist precisely for the case a source is unavailable or flaky — the moment a live interface is least trustworthy — and a non-reproducible classification would make the manifest failure scope, `cne retry`, and the compact gate unstable across runs.

#### Scenario: classification is fully offline
- **WHEN** a symbol batch is being classified and `trading_status`, `instruments`, `daily_bars`, and the delisted catalogue are all present on disk
- **THEN** the exemption decision is made from on-disk data alone, with no network request

#### Scenario: source outage does not change the decision
- **WHEN** a vendor (e.g. EastMoney or TDX) is down during a run
- **THEN** exemption classification still succeeds against persisted evidence, and the run's failure scope is determined without consulting the down source

### Requirement: Rerun never overrides a run's recorded granularity

`cne retry --run-id` SHALL reconcile the run's failed batches under the granularity recorded when the run started (per the "Rerun / retry follows the run's active granularity" requirement). There SHALL be no granularity override entry anywhere in the CLI (neither on `cne retry` nor on `cne run daily`/`cne backfill`), so a run can never be reconciled under a different mode than the one recorded at its start.

The granularity switch SHALL affect only the worker-pool TDX path (`fetch_daily_bars_parallel`) and its gap-fill. The dedicated history/recovery paths that already run per symbol — `step_daily_bars_history` (同花顺) and `step_daily_bars_delisted` (baostock) — SHALL keep symbol-level semantics regardless of the switch.

#### Scenario: a retry uses the run-recorded granularity despite current config
- **WHEN** a run recorded in `batch` mode is retried with `cne retry --run-id` while `daily_bars_granularity` in the current config is `"symbol"`
- **THEN** the retry reconciles under the recorded `batch` semantics (whole-batch refetch) and no override entry can change that

#### Scenario: history/recovery paths are untouched by the switch
- **WHEN** `daily_bars_granularity = "batch"` is set and `step_daily_bars_history` or `step_daily_bars_delisted` runs
- **THEN** those paths continue per-symbol segment-and-fail semantics, with no batch all-or-nothing behavior introduced

### Requirement: Switching granularity is run-scoped and non-destructive

Switching `daily_bars_granularity` between `symbol` and `batch` SHALL NOT require or trigger deletion, rewrite, or replay of existing staging, manifest batches, curated rows, or watermarks. Each run is isolated by `run_id`, manifest batches by `(run_id, batch_id)`, and the compact gate is evaluated per run. An abandoned `symbol`-mode run with an unresolved failure scope SHALL keep its partial staging until reclaimed by `cne clean`. Windows already compacted SHALL NOT be re-fetched on a mode switch; re-applying the new mode to an existing window SHALL be a deliberate explicit backfill.

#### Scenario: a mode switch is a no-op on stored data
- **WHEN** an operator toggles `daily_bars_granularity` from `symbol` to `batch` and runs a fresh `cne run daily`
- **THEN** curated rows, existing watermarks, and prior manifest batches remain untouched, and the new run stages under a new `run_id`

#### Scenario: abandoned symbol-mode staging is reclaimed only via clean
- **WHEN** a `symbol`-mode run staged partial rows with an unresolved failure scope and is abandoned, then `cne clean --force` is run
- **THEN** the orphaned partial staging is removed and no new run depends on it