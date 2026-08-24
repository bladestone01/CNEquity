## ADDED Requirements

### Requirement: Backup path is config-gated

The system SHALL use the baostock backup path for `trading_status` only when all of the following hold:

1. `config.failover_enabled` is true
2. a `[[failover.datasets]]` entry with `name = "trading_status"`, `primary = "eastmoney"`, `backup = "baostock"` exists
3. the backup source is enabled (`config.sources` for `baostock`)

When any gate is absent, the system MUST behave exactly as before (EastMoney only, `_fail_or_mock` on failure).

#### Scenario: Backup gated off
- **WHEN** the `[[failover.datasets]]` entry for `trading_status` is absent
- **THEN** a primary failure raises the unchanged `TdxSourceError` and the step fails

#### Scenario: Backup gated on and primary healthy
- **WHEN** the gates hold and the EastMoney primary fetch succeeds
- **THEN** the baostock backup adapter is NOT invoked

### Requirement: Universe is split between SH/SZ and BJ on fallback

When the backup path is exercised, the system SHALL produce one row for every requested symbol:

- SH/SZ symbols SHALL be classified from `query_all_stock(day)` (`tradeStatus=0` → `is_trading=false`, `status="suspended"`; A-share name prefix containing `ST`/`*ST` → `status="st"`; otherwise `normal`).
- BJ symbols SHALL first be attempted via the EastMoney suspension leg; if that leg fails, they SHALL default to `is_trading=true, status="normal"` and be counted in an audit finding.

The combined frame MUST pass the existing `observed == expected` completeness check for the requested universe.

#### Scenario: Split universe fallback with BJ defaulted
- **WHEN** EastMoney is down, baostock serves SH/SZ, and the EastMoney suspension leg also fails for BJ
- **THEN** all requested BJ symbols receive `normal` rows and `n_bj_defaulted` is recorded in audit findings

#### Scenario: Baostock query fails entirely
- **WHEN** `query_all_stock(day)` raises or returns an unusable response
- **THEN** the backup is refused and the step raises `TdxSourceError`

### Requirement: Backup data must be current for the trade date

The system SHALL verify, before using the backup, that baostock has processed the requested trade date `D` (e.g. a reference-symbol probe whose k-data reaches `D`). If baostock only has data through a prior day, the system MUST refuse the backup rather than write a stale status stamped with `trade_date = D`.

#### Scenario: Baostock data is stale
- **WHEN** the freshness probe shows baostock has not yet processed day `D`
- **THEN** no backup rows are written and the step fails with an explicit "backup stale" reason

### Requirement: Missing SH/SZ rows are classified, not blindly filled

For requested SH/SZ symbols absent from the `query_all_stock(day)` snapshot, the system SHALL classify before filling:

- symbols whose previous curated row was `status="suspended"` MUST NOT be silently filled as `normal` (no real suspension is washed to tradeable) — the absence MUST either be surfaced as a fill failure or propagate a per-symbol flag.
- other missing rows MAY be filled as `normal`, but every fill MUST be counted in audit findings.
- when the total filled-default count exceeds a threshold (default `max(50, 1% of universe)`), the backup MUST be refused.

#### Scenario: Previously suspended symbol goes missing
- **WHEN** a symbol was `suspended` on the previous curated day and is absent from today's baostock snapshot
- **THEN** the system does not emit a `normal` row for it and records a fill-failure

#### Scenario: Fill threshold exceeded
- **WHEN** filled-default rows exceed the configured threshold
- **THEN** the backup is refused and the step fails rather than serving a degraded snapshot

### Requirement: Degraded runs are provenance-tagged and auditable

When the backup produced the frame, the system SHALL:

- stamp provenance `source` reflecting the true origin (baostock), not `eastmoney`
- write the backup frame to `meta/source_snapshots` for `trading_status`
- report the step as `status="warning"` with `context_updates.audit_findings` summarizing `n_filled`, `n_bj_defaulted`, and the freshness check outcome

#### Scenario: Backup used and audited
- **WHEN** the step completes via the baostock backup
- **THEN** the curated frame is stamped baostock, a source snapshot exists, and the run's audit findings describe the degradation

### Requirement: Output conforms to the trading_status schema

Backup rows SHALL have columns `symbol` (canonical `NNNNNN.SS` format), `trade_date` (`Date`), `is_trading` (`Boolean`), `status` with vocabulary restricted to `{normal, st, suspended}`; the four metadata columns (`source`, `data_version`, `fetched_at`) are appended by existing write paths.

#### Scenario: Schema validation
- **WHEN** a backup frame is produced
- **THEN** it validates against `TRADING_STATUS_SCHEMA` exactly as the primary path does