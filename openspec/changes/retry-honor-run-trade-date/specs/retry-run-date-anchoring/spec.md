## ADDED Requirements

### Requirement: Retry re-anchors date-bound steps to the run's recorded trade date

`cne retry --run-id` SHALL re-run date-anchored (non worker-batch) steps against the `trade_date` recorded in the run's metadata when present, instead of the current wall-clock date. The recorded value SHALL take precedence over the caller-supplied/default today, so a retry replays the original run's anchor exactly.

#### Scenario: date-anchored step retried on the original date
- **WHEN** a run recorded `trade_date = 2026-08-21` fails and is retried with `cne retry --run-id` while the system date is 2026-08-22
- **THEN** date-anchored steps such as `trading_status` are re-run for 2026-08-21, not 2026-08-22

#### Scenario: retry without a recorded trade date keeps today
- **WHEN** a run has no `trade_date` in its metadata (legacy/manual run)
- **THEN** the retry falls back to the caller-supplied date (`shanghai_today()` default), with behavior unchanged

### Requirement: Worker-batch retry windows stay manifest-driven

The re-anchoring SHALL NOT affect worker-batch steps (`daily_bars`): their retry windows continue to come from each batch's manifest `window_start`/`window_end`, unchanged.

#### Scenario: daily_bars retry window unchanged
- **WHEN** a failed `daily_bars` batch with window 2026-08-20..2026-08-21 is retried after this change
- **THEN** the retry still refetches the manifest-designated window and no new trade-date logic is applied

### Requirement: Corrupt recorded dates cannot break a retry

If the recorded `trade_date` is missing or unparsable, the retry SHALL proceed with the fallback date rather than raising, so metadata issues never turn a recoverable run into a hard failure.

#### Scenario: malformed recorded date falls back
- **WHEN** a run's metadata `trade_date` is not a valid ISO date
- **THEN** the retry ignores it, uses the fallback date, and completes normally (steps still run)