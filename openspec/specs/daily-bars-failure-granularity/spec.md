## ADDED Requirements

> Scope: the requirements in this document describe `symbol`-mode behavior (the default, `daily_bars_granularity = "symbol"`). Under `batch` mode (`daily_bars_granularity = "batch"`) the legacy strict all-or-nothing semantics apply unchanged — a batch with any missing symbol fails as a whole with no partial staging. The mode switch and its guarantees are specified in `daily-bars-processing-granularity`.

### Requirement: Missing bars are attributed per symbol, not per batch

A `daily_bars` symbol batch SHALL not be discarded as a whole because one symbol returned no TDX rows. Symbols that did return bars SHALL be staged from TDX, and only the symbols genuinely missing rows SHALL be recorded as failed and routed to the existing failover.

#### Scenario: one of a hundred symbols has no bars
- **WHEN** a 100-symbol batch produces rows for 99 symbols but zero rows for the 100th
- **THEN** the 99 symbols' rows are staged normally and the single failing symbol alone is reported to the failover path

#### Scenario: whole batch healthy
- **WHEN** every symbol in the batch returns its expected rows
- **THEN** the batch progresses exactly as today (no failover, no failed symbols)

### Requirement: Legitimate-empty symbols are exempt, not failures

A symbol SHALL NOT fail its batch nor be routed to failover when its absence of bars in the window is expected:

- not yet listed: `list_date` is populated and strictly after the window end;
- already delisted: `delist_date` is populated and strictly before the window start;
- suspended for the whole window: no trading session of the window had a bar and the symbol is classified suspended across it.

Exempted symbols SHALL be dropped from the required set, recorded as a finding (count only), and never trigger a failover re-fetch.

#### Scenario: symbol listed after the window
- **WHEN** an instrument's `list_date` is after the window end
- **THEN** the symbol is exempted from the required set and no failover is triggered

#### Scenario: symbol delisted before the window
- **WHEN** an instrument's `delist_date` is before the window start
- **THEN** the symbol is exempted from the required set and no failover is triggered

#### Scenario: symbol suspended for the whole window
- **WHEN** the symbol was not trading on any session in the window
- **THEN** the symbol is exempted (finding only) instead of failing the batch

### Requirement: Unexpected gaps remain strict

A requested stock that is within its listing window, was trading (or is not demonstrably exempt) in the window, and returns no TDX rows SHALL still fail loudly and be routed through the existing failover, so the "silent placeholder masks a real vendor gap" failure mode is not re-introduced.

#### Scenario: genuine vendor gap
- **WHEN** a listed, non-exempt stock returns no bars and the window has trading sessions for it
- **THEN** the symbol is reported failed and the existing tip clist / multi-day kline failover refetches it
