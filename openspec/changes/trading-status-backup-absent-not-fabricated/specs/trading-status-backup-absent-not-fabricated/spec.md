## ADDED Requirements

### Requirement: Snapshot-missing symbols with no prior record are left absent, not fabricated

When the trading_status backup (baostock `query_all_stock`) omits an SH/SZ A-share from its daily snapshot and the symbol has no prior curated status record, the backup SHALL NOT emit a fabricated `status="normal"` row. The symbol SHALL be left absent for that trade date (no row in the output/curated), so an unobserved symbol is never claimed as trading normally. The other two branches SHALL be preserved unchanged: a prior non-tradable status still refuses the whole backup (no wash), and a prior `normal` status still carries forward.

#### Scenario: never-recorded symbol missing from snapshot is absent
- **WHEN** a symbol is not present in the baostock daily snapshot and has no prior curated status
- **THEN** no row is emitted for that symbol×date, and the day's output contains no fabricated normal for it

#### Scenario: previously normal symbol still carries forward
- **WHEN** a symbol is missing from the snapshot but its prior curated status is `normal`
- **THEN** it is still carried forward as `normal`, unchanged

#### Scenario: previously non-tradable symbol still refuses the backup
- **WHEN** a symbol missing from the snapshot has a prior `st`/`*st`/`suspended` status
- **THEN** the whole backup fill is refused (fill-failure), unchanged

### Requirement: Missing-unseen count is observable without touching data

The backup meta SHALL include `n_missing_unseen` — the number of SH/SZ A-share symbols left absent under the previous requirement — and the step SHALL surface a non-zero value as an audit finding, so operators can see how many never-recorded symbols were omitted without polluting the data with a fabricated status or a new status vocabulary.

#### Scenario: count surfaced when unseen omissions occur
- **WHEN** the backup omits N never-recorded symbols and the backup is accepted
- **THEN** `fetch_trading_status_backup` meta carries `n_missing_unseen == N` and `step_trading_status` records a finding naming the count (and no finding when N == 0)

#### Scenario: no new status vocabulary
- **WHEN** the backup accepts a day that had unseen omissions
- **THEN** no new status value is introduced and `_NON_TRADABLE_STATUSES` is untouched; the omitted symbols simply have no row

### Requirement: Guardrails and gating are unchanged

The change SHALL NOT alter the fill threshold (`max(50, universe//100)`), the freshness gate, the empty-snapshot refusal, the wash-guard, schema, CLI, or the compact gate. Because `n_filled` no longer counts never-recorded omissions, the threshold continues to bound only genuinely carried-forward fabrications.

#### Scenario: threshold still bounds fabricated rows only
- **WHEN** a backup accepts a day
- **THEN** `n_filled` (carried-forward rows) still obeys the existing threshold, and never-recorded omissions are reported separately via `n_missing_unseen` rather than inflating `n_filled`