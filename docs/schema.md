# Schema Contract

StockDataEngine curated datasets share provenance columns and explicit primary keys.

## Global conventions

| Rule | Value |
|------|-------|
| Timezone | `Asia/Shanghai` for all `trade_date` and business timestamps |
| Symbol | `{code}.{SH\|SZ\|BJ}` e.g. `600519.SH` |
| Exchange column | `SH`, `SZ`, or `BJ` |
| Provenance columns | `source`, `data_version`, `fetched_at` (UTC ISO8601) on every curated row |
| Null semantics | Suspended days: OHLCV present, `volume=0`, `amount=0` |
| Schema evolution | Additive columns only; breaking changes bump `dataset_schema_version` |

## Partition keys (curated)

| Dataset | Partition |
|---------|-----------|
| daily_bars | `trade_date` |
| index_bars | `trade_date` |
| minute_bars | `frequency`, `trade_date`, `symbol_bucket` |
| trading_status | `trade_date` |
| corporate_actions | `ex_date` (year-month) |
| adj_factors | `trade_date` |
| financial_statement_items | `report_period` |
| industry_members | `as_of_date` |
| northbound_flows | `trade_date` |

Multi-source snapshots: `meta/source_snapshots/{dataset}/source={source}/data_version={ver}/`

## Primary keys

| Dataset | Primary key |
|---------|-------------|
| instruments | `(symbol)` |
| trading_calendar | `(trade_date)` |
| trading_status | `(symbol, trade_date)` |
| daily_bars | `(symbol, trade_date)` |
| index_bars | `(symbol, trade_date, frequency)` |
| minute_bars | `(symbol, trade_date, bar_time, frequency)` |
| corporate_actions | `(symbol, ex_date, action_type)` |
| adj_factors | `(symbol, trade_date, adjust_type)` |
| fund_flow | `(symbol, trade_date)` |
| northbound_holdings | `(symbol, trade_date, channel)` |
| northbound_flows | `(trade_date, channel)` |
| margin_trading | `(symbol, trade_date)` |
| sector_members | `(symbol, sector_code, as_of_date)` |
| valuation_metrics | `(symbol, trade_date)` |
| announcement_index | `(announcement_id)` |
| financial_statement_items | `(symbol, report_period, statement_type, item_code)` |
| industry_members | `(symbol, classification_system, as_of_date)` |

## MVP-P0 column definitions

### instruments

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | PK |
| name | string | |
| exchange | string | SH/SZ/BJ |
| asset_type | string | stock/etf/index |
| list_date | date | nullable |
| delist_date | date | nullable |
| prev_symbol | string | nullable |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

### trading_calendar

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | PK |
| is_trading | bool | |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

### trading_status

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| is_trading | bool | |
| status | string | normal/suspended/st/*st |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

### daily_bars

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| open | float64 | unadjusted |
| high | float64 | |
| low | float64 | |
| close | float64 | |
| volume | int64 | shares |
| amount | float64 | CNY |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

### index_bars

Same as daily_bars plus `frequency` (default `1d`), `asset_type=index`.

### corporate_actions

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| ex_date | date | |
| action_type | string | cash_dividend/bonus/transfer/allotment |
| cash_dividend | float64 | per share |
| bonus_ratio | float64 | per 10 shares |
| transfer_ratio | float64 | per 10 shares |
| allotment_ratio | float64 | nullable |
| allotment_price | float64 | nullable |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

### adj_factors

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| adjust_type | string | qfq/hfq |
| factor | float64 | cumulative factor; qfq: `1/sina_qfq_factor`, hfq: `sina_hfq_factor` |
| source | string | sina (default) |
| data_version | string | |
| fetched_at | timestamp | |

## Compact deduplication

On compact: group by primary key, keep row with max(`fetched_at`).

## DuckDB views

```sql
CREATE VIEW daily_bars_view AS
SELECT * FROM read_parquet('{root}/curated/daily_bars/**/*.parquet', hive_partitioning=true);

CREATE VIEW daily_bars_adj AS
SELECT b.*, b.close * a.factor AS adj_close
FROM daily_bars_view b
LEFT JOIN read_parquet('{root}/derived/adj_factors/**/*.parquet', hive_partitioning=true) a
  ON b.symbol = a.symbol AND b.trade_date = a.trade_date AND a.adjust_type = 'qfq';
```
