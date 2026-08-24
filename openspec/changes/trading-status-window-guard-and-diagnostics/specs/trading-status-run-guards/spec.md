## ADDED Requirements

### Requirement: Already-covered days short-circuit with a clear message

`step_trading_status` SHALL first check whether the trade date already has complete curated coverage: the `trading_status` curated partition holds rows for the date and the observed symbol set is a superset of the requested universe. When covered, the step SHALL NOT perform any network fetch, SHALL log a "数据已补齐成功" message, SHALL emit a finding with `check="already_completed"` (severity info), and SHALL return success with zero rows.

#### Scenario: rerun after a successful evening capture
- **WHEN** the day's curated `trading_status` already covers the full requested universe
- **THEN** the step skips fetching, logs the already-completed message, and returns success with no rows

#### Scenario: partial coverage must not short-circuit
- **WHEN** curated rows exist for the date but the observed symbol set is smaller than the requested universe
- **THEN** the step proceeds to fetch (short-circuit is only for complete coverage)

### Requirement: Pre-finalization hours skip the capture with an advisory message

For the current trading day before 16:00 Asia/Shanghai (and not already covered per the previous requirement), `step_trading_status` SHALL skip the data collection action, log an advisory "非正常数据读取时间段（16:00+ 之后）" message, emit a finding with `check="before_cutoff"`, and return `status="warning"` with zero rows. Historical dates, non-trading days, and the backfill path (`_backfill`) SHALL NOT be affected.

#### Scenario: morning run on a trading day
- **WHEN** the current trading day is fetched before 16:00 Asia/Shanghai and is not yet covered
- **THEN** the step skips collection and reports `before_cutoff` instead of attempting a fetch

#### Scenario: evening run on the same day
- **WHEN** the current trading day is fetched at/after 16:00 Asia/Shanghai
- **THEN** the step proceeds with the normal primary/backup fetch

#### Scenario: historical or non-trading day
- **WHEN** the trade date is before today or today is not a trading day
- **THEN** the time-window guard does not apply and the step fetches normally

### Requirement: Backup-decline reasons surface in the failure

When the primary fails and the backup coordinator declines (`frame is None`), the raised error SHALL include the coordinator's refusal reason alongside the primary failure: `trading_status: primary(eastmoney) failed; backup declined: <reason>` (chained from the original exception). The reason SHALL come from the coordinator metadata (e.g. `stale`, `not configured`, `fill threshold exceeded`, `fill-failure`, `empty snapshot`).

#### Scenario: backup refused because baostock is stale
- **WHEN** the coordinator returns `(None, {"reason": "baostock has no data for D yet"})`
- **THEN** the raised error message contains both the primary failure and that decline reason

#### Scenario: backup not configured
- **WHEN** no `[[failover.datasets]]` entry for `trading_status` exists and the primary fails
- **THEN** the raised error says the backup was declined with the not-configured reason