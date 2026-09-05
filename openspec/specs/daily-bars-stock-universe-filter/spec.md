## ADDED Requirements

### Requirement: Tip sweep universe is restricted to equity instruments

The daily tip sweep for `daily_bars` SHALL fetch bars only for instruments whose `asset_type` is `"stock"`. Funds, ETFs, REITs and any other non-equity `asset_type` SHALL NOT be part of the `step_daily_bars` sweep universe, matching the criterion already applied by `step_daily_bars_history`.

#### Scenario: ETF present in instruments is not swept
- **WHEN** an instrument such as `562110.SH` (`asset_type="etf"`) is in the instruments table
- **THEN** the daily tip sweep excludes it, so its absence of TDX bars cannot fail its symbol batch

#### Scenario: ordinary A-share stocks are still swept
- **WHEN** an instrument has `asset_type="stock"` (e.g., `600519.SH`)
- **THEN** it remains in the tip sweep universe exactly as before

### Requirement: Stock coverage stays strict for unexpected gaps

The per-symbol coverage guarantee SHALL remain for stock instruments with a genuine gap: a stock that is within its listing window, is not exempted as legitimate-empty (not-yet-listed / delisted / fully-suspended, see `daily-bars-failure-granularity`), and returns no TDX rows in the window SHALL fail and be routed through the existing failover (EastMoney clist for the tip, kline for multi-day windows).

#### Scenario: genuine stock gap routes to failover
- **WHEN** a stock like `301655.SZ` is within its listing window but TDX returns no rows
- **THEN** the symbol alone is reported failed and the existing failover refetches it — the rest of its batch is staged normally
