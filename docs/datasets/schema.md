# Schema 契约

StockDataEngine curated datasets share provenance columns and explicit primary keys.

### Global conventions

| Rule | Value |
|------|-------|
| Timezone | `Asia/Shanghai` for all `trade_date` and business timestamps |
| Symbol | `{code}.{SH\|SZ\|BJ}` e.g. `600519.SH` |
| Exchange column | `SH`, `SZ`, or `BJ` |
| Provenance columns | `source`, `data_version`, `fetched_at` (UTC timestamp) on every curated row |
| Null semantics | Suspended days: OHLCV present, `volume=0`, `amount=0` |
| Schema evolution | Additive columns only; breaking changes bump `dataset_schema_version` |

### Partition keys (curated)

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

### Primary keys

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

### MVP-P0 column definitions

#### instruments

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

#### trading_calendar

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | PK |
| is_trading | bool | |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### trading_status

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| is_trading | bool | |
| status | string | normal/suspended/st/*st |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### daily_bars

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

#### index_bars

Same as daily_bars plus `frequency` (default `1d`), `asset_type=index`.

#### corporate_actions

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| ex_date | date | |
| action_type | string | cash_dividend/bonus/transfer/allotment |
| cash_dividend | float64 | **per share** (yuan, pretax) |
| bonus_ratio | float64 | **per share** (送股: new shares per held share) |
| transfer_ratio | float64 | **per share** (转股: new shares per held share) |
| allotment_ratio | float64 | **per share** (配股: offered shares per held share), nullable |
| allotment_price | float64 | per allotted share (yuan), NOT a ratio, nullable |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

> **Unit contract (per-share).** All ratios/amounts are per ONE held share, not
> the raw "每10股" convention that TDX (`xdxr`) and EastMoney quote. Adapters
> divide raw per-10-share source values by 10 before staging (e.g. "10派8.5元"
> → 0.85, "10送8股" → 0.8, "10转4股" → 0.4, "10配3股" → 0.3). Downstream
> real-share accounting is uniform with no /10 magic numbers:
> `shares_after = shares × (1 + bonus_ratio + transfer_ratio)`,
> `cash = shares × cash_dividend`. `allotment_price` is a per-share price, not
> a ratio, and is left un-divided. Note: TDX `xdxr` does not split 送 vs 转 —
> it puts the combined 送转 total into `bonus_ratio` (transfer_ratio=0); the
> total mult is exact but the 送/转 split is only distinguished on EM daily.

#### adj_factors

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| adjust_type | string | qfq/hfq |
| factor | float64 | cumulative factor; qfq: `1/sina_qfq_factor`, hfq: `sina_hfq_factor` |
| source | string | sina (default) |
| data_version | string | |
| fetched_at | timestamp | |

#### financial_statement_items

Point-in-time (PIT) queries **must** filter on `announce_date <= as_of` at read time
(`load(..., as_of=)`); never align fundamentals by `report_period` alone.

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| report_period | string | e.g. ``2024Q1`` |
| statement_type | string | income / balance / cashflow |
| item_code | string | e.g. ``roe``, ``revenue`` |
| item_value | float64 | |
| announce_date | date | **PIT axis** — public disclosure date |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### fund_flow

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| main_net_inflow | float64 | CNY |
| super_large_net_inflow | float64 | |
| large_net_inflow | float64 | |
| medium_net_inflow | float64 | |
| small_net_inflow | float64 | |
| source / data_version / fetched_at | | provenance |

#### margin_trading

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| margin_balance | float64 | |
| margin_buy | float64 | |
| short_balance | float64 | |
| short_sell_volume | float64 | |
| source / data_version / fetched_at | | provenance |

#### northbound_holdings

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| channel | string | SH / SZ connect |
| holding_shares | float64 | |
| holding_mv | float64 | |
| holding_ratio | float64 | |
| source / data_version / fetched_at | | provenance |

#### northbound_flows

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | |
| channel | string | SH / SZ |
| net_buy | float64 | |
| buy_amount | float64 | |
| sell_amount | float64 | |
| source / data_version / fetched_at | | provenance |

#### valuation_metrics

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| pe_ttm | float64 | |
| pb | float64 | |
| ps_ttm | float64 | |
| total_mv | float64 | |
| float_mv | float64 | |
| source / data_version / fetched_at | | provenance |

#### sector_members

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| sector_code | string | |
| sector_name | string | |
| as_of_date | date | snapshot date |
| source / data_version / fetched_at | | provenance |

#### announcement_index

PIT queries filter `announce_date <= as_of`.

| Column | Type | Notes |
|--------|------|-------|
| announcement_id | string | PK |
| symbol | string | |
| title | string | |
| announce_date | date | **PIT axis** |
| category | string | |
| url | string | |
| source / data_version / fetched_at | | provenance |

#### earnings_disclosure_schedule

预约披露时间表（EM datacenter `RPT_PUBLIC_BS_APPOIN`，镜像沪深交易所披露日历）。
现值语义、非 PIT：预约变更覆盖 `scheduled_date`，`first_scheduled_date` 保留首次预约，
`actual_date` 实际披露后回填（此前为 null）。

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| report_period | string | e.g. ``2026Q2``（分区键） |
| scheduled_date | date | 当前有效预约披露日 |
| first_scheduled_date | date | 首次预约披露日 |
| actual_date | date | 实际披露日，未披露为 null |
| source / data_version / fetched_at | | provenance |

#### dragon_tiger

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| reason | string | |
| buy_amount | float64 | |
| sell_amount | float64 | |
| net_amount | float64 | |
| source / data_version / fetched_at | | provenance |

#### block_trades

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| price | float64 | |
| volume | float64 | |
| amount | float64 | |
| premium_ratio | float64 | discount vs close |
| source / data_version / fetched_at | | provenance |

#### index_constituents

| Column | Type | Notes |
|--------|------|-------|
| index_symbol | string | e.g. ``000300.SH`` |
| symbol | string | constituent |
| as_of_date | date | snapshot / rebalance date |
| weight | float64 | index weight (percent or ratio per source) |
| source / data_version / fetched_at | | provenance |

#### industry_members

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| classification_system | string | e.g. ``sw``, ``eastmoney`` |
| industry_code | string | |
| industry_name | string | |
| as_of_date | date | classification snapshot |
| source / data_version / fetched_at | | provenance |

#### macro_indicators

| Column | Type | Notes |
|--------|------|-------|
| indicator_id | string | e.g. ``shibor_3m``, ``cnbond_yield_10y``, ``lpr_1y`` |
| obs_date | date | observation / release date |
| value | float64 | |
| frequency | string | ``daily`` / ``monthly`` |
| source / data_version / fetched_at | | provenance |

#### market_breadth

Computed from curated ``daily_bars`` vs prior trading day.

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | |
| metric_id | string | ``advance_count``, ``decline_count``, ``limit_up_count``, … |
| value | float64 | |
| source / data_version / fetched_at | | provenance |

#### share_unlock_schedule

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| unlock_date | date | scheduled unlock |
| unlock_shares | float64 | |
| unlock_ratio | float64 | fraction of float/total per source |
| unlock_type | string | e.g. IPO lock-up, private placement |
| source / data_version / fetched_at | | provenance |

#### regulatory_events

| Column | Type | Notes |
|--------|------|-------|
| event_id | string | PK |
| symbol | string | |
| event_date | date | announcement date |
| event_type | string | ``penalty``, ``investigation``, ``regulatory_letter``, … |
| title | string | |
| source / data_version / fetched_at | | provenance |

#### institutional_holdings

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| holder_type | string | ``fund``, ``qfii``, ``social_security``, … |
| report_period | string | e.g. ``2024Q1`` |
| holding_shares | float64 | holder count or share volume per source |
| holding_ratio | float64 | pct of float/total |
| holding_mv | float64 | market value |
| source / data_version / fetched_at | | provenance |

#### analyst_consensus

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| forecast_date | date | publish / update date |
| forecast_year | int64 | target fiscal year |
| eps_forecast | float64 | consensus EPS |
| pe_forecast | float64 | implied PE |
| target_price | float64 | avg target price |
| rating | string | e.g. 买入/增持 |
| analyst_count | int64 | covering institutions |
| source / data_version / fetched_at | | provenance |

#### sentiment_scores

Dual channels: ``announcement_keywords`` (公告标题) and ``stock_news_nlp`` (EastMoney 个股新闻 + keyword/SnowNLP).

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| score_channel | string | PK axis; ``announcement_keywords`` / ``stock_news_nlp`` |
| sentiment_score | float64 | [-1, 1] |
| headline_count | int64 | headlines scored |
| source / data_version / fetched_at | | provenance |

#### stock_news (on-demand cache)

Cached JSON at ``meta/on_demand/stock_news/{symbol}.json``; fetched via ``sde query --dataset stock_news --symbol``.

| Field | Type | Notes |
|-------|------|-------|
| symbol | string | |
| items[].news_id | string | |
| items[].title | string | |
| items[].publish_time | string | |
| items[].publish_date | string | ISO date when parseable |
| items[].sentiment_score | float64 | per-headline NLP score |
| items[].sentiment_method | string | ``keyword`` / ``snownlp`` / ``keyword+snownlp`` |
| aggregate_sentiment | float64 | mean of item scores |
| headline_count | int64 | |
| source / data_version / fetched_at | | provenance |

### Compact deduplication

On compact: group by primary key, keep row with max(`fetched_at`).

### DuckDB views

```sql
CREATE VIEW daily_bars_view AS
SELECT * FROM read_parquet('{root}/curated/daily_bars/**/*.parquet', hive_partitioning=true);

CREATE VIEW daily_bars_adj AS
SELECT b.*, b.close * a.factor AS adj_close
FROM daily_bars_view b
LEFT JOIN read_parquet('{root}/derived/adj_factors/**/*.parquet', hive_partitioning=true) a
  ON b.symbol = a.symbol AND b.trade_date = a.trade_date AND a.adjust_type = 'qfq';
```

