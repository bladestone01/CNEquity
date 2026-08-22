## ADDED Requirements

### Requirement: Suspension query satisfies current datacenter contract

The EastMoney suspension fetch SHALL query report `RPT_CUSTOM_SUSPEND_DATA_INTERFACE` using the contract reverse-engineered on 2026-08:

- filter SHALL be `(DATETIME='<D>')` + `(MARKET="<market>")` — `DATETIME` in single quotes (virtual batch/date field, not an output column), `MARKET` in double quotes.
- five markets SHALL each be queried and deduplicated by `SECURITY_CODE`: `沪市A股`, `深市A股`, `科创板`, `创业板`, `京市A股`.
- output columns SHALL be limited to what coverage needs: `SECURITY_CODE, SUSPEND_START_DATE, SUSPEND_END_TIME`（the old `STOP_DATE`/`RESUME_DATE` no longer exist，不得请求；`SUSPEND_EXPIRE/SUSPEND_REASON/PREDICT_RESUME_DATE/SECURITY_NAME_ABBR/TRADE_MARKET` 属元数据，按项目决策不入 `TRADING_STATUS_SCHEMA`，不请求/丢弃）。

A genuine schema rejection (`success=false`, e.g. `code=9501`) SHALL raise `EastMoneyDatacenterError` via the existing datacenter machinery, never be treated as "no suspensions".

#### Scenario: Filter matches current contract
- **WHEN** a query is sent with `(DATETIME='2026-08-19')(MARKET="沪市A股")` and valid output columns
- **THEN** the report returns suspension rows with the new columns for that market

#### Scenario: Old columns requested
- **WHEN** the fetch requests `STOP_DATE` or `RESUME_DATE`
- **THEN** the server rejects with a "返回字段不存在" schema error and the adapter raises rather than returning an empty frame

### Requirement: Empty batch is not silently "no suspensions"

The fetch SHALL fail loudly (raise `EastMoneyDatacenterError` / fail the leg) when **all five markets** return an empty `DATETIME` batch for a trading day, because an empty batch is ambiguous (server-side data not yet generated, or transient) and MUST NOT be converted into "every symbol is trading". A batch that is only empty for some markets SHALL log a warning and continue with the non-empty markets.

#### Scenario: all markets empty
- **WHEN** every market query returns `9201 返回数据为空` for trade date `D`
- **THEN** the suspension leg raises and the step treats the primary as failed

#### Scenario: subset of markets empty
- **WHEN** only, e.g., `科创板` returns empty while `沪市A股`/`深市A股` return rows
- **THEN** the fetch continues with the non-empty results and logs the gap

### Requirement: Suspension coverage semantics are preserved

A symbol SHALL be classified `status="suspended"` for trade date `D` iff its suspension window covers `D`: `SUSPEND_START_DATE <= D` and (`SUSPEND_END_TIME` null or `>= D`). Symbols whose window does not cover `D` MUST NOT be flagged suspended.

#### Scenario: Open-ended suspension
- **WHEN** a row has `SUSPEND_END_TIME` null and `SUSPEND_START_DATE <= D`
- **THEN** the symbol is suspended for `D`

#### Scenario: Resumed before the trade date
- **WHEN** a row has `SUSPEND_END_TIME < D`
- **THEN** the symbol is classified `normal` for `D`, not suspended