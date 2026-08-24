## ADDED Requirements

### Requirement: Expected gap failures are reported as a step status, not an exception

When a multi-day `daily_bars` window still has unresolved symbol×date keys after gap-fill, `_finish_daily_bars` SHALL NOT raise a generic `RuntimeError`. It SHALL return `status: "failed"` together with a structured payload (unresolved symbols, missing-key count, failed batches) so the engine records the step as failed through its normal status channel. Genuine unexpected failures (schema violations, internal invariant breaks, gap-fill adapter errors) SHALL still raise.

#### Scenario: unresolved gap returns a failed step status
- **WHEN** a multi-day window has symbols that remain missing after EastMoney kline gap-fill
- **THEN** `step_daily_bars` completes with `status: "failed"` carrying `unresolved_symbols`, `missing_keys`, and `failed_batches`, and does not raise `RuntimeError`

#### Scenario: a genuine bug still raises
- **WHEN** an unexpected programming error occurs inside the step (e.g. schema validation failure, invariant violation)
- **THEN** the exception is raised as before and the engine records the step as failed via the exception path

### Requirement: The failure payload names unresolved symbols and failed batches

The returned failure payload SHALL identify the scope of the gap: `unresolved_symbols` (symbols still missing), `missing_keys` (count of symbol×date gaps), and `failed_batches` (list of `{batch_id, symbol_count, sample_symbols}` derived from the manifest). These fields SHALL be present in the run result so scripts and `cne status` consumers can act on them programmatically rather than parsing an error message.

#### Scenario: run result carries structured failure detail
- **WHEN** the daily run ends with the daily_bars step in `failed` status
- **THEN** the run results for that step contain `unresolved_symbols`, `missing_keys`, and `failed_batches` with non-empty values naming the actual failing symbols and batches

### Requirement: Expected failures are surfaced as actionable logs without a traceback

An expected coverage-gap failure SHALL NOT emit a full traceback. The engine SHALL log `Step daily_bars failed in <elapsed>s`, and a single `ERROR`-level line SHALL name the unresolved count, a sample of unresolved symbols, the failing batch ids with their symbol counts, and a `cne retry --run-id` hint. The line SHALL remain visible under `--quiet`.

#### Scenario: actionable error log, no backtrace
- **WHEN** a multi-day gap failure occurs
- **THEN** the log contains one ERROR line with `missing` count, sample symbols, `failed batches: <batch_id>: N symbol(s)`, and a `cne retry --run-id` hint, and no `Traceback (most recent call last)` frame for that expected failure

### Requirement: Strictness, persistency, and retry semantics are preserved

Changing the reporting channel SHALL NOT change the failure's consequences: the run still ends `failed`, an unresolved batch remains `failed` in the manifest (so `compact` keeps blocking the `daily_bars` dataset and the watermark does not advance), and `cne retry --run-id` still recomputes the failure-scoped refetch from `failed_scope_json`. The durable sources for audit/retry (`failed_scope_json`, `daily_bars_kline_gapfill` findings) remain unchanged.

#### Scenario: compact gate and retry remain gated by the batch
- **WHEN** a daily run reports daily_bars `failed` via the status channel
- **THEN** the unresolved batches stay `failed` in the manifest, `compact` skips `daily_bars` for that run, and `cne retry --run-id` refetches only the recorded failure scope

#### Scenario: CLI contract unchanged
- **WHEN** `cne run daily` runs and the daily_bars step fails with unresolved gaps
- **THEN** the command prints `{"run_id": ..., "status": "failed"}` and exits non-zero, with no new `--granularity` or other CLI parameter introduced