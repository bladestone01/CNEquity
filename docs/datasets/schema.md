# Schema 契约

ashare-lake 的 curated 数据集统一带溯源列，并声明明确主键。

### 全局约定

| 规则 | 取值 |
|------|-------|
| 时区 | 所有 `trade_date` 与业务时间戳使用 `Asia/Shanghai` |
| Symbol | `{code}.{SH\|SZ\|BJ}`，如 `600519.SH` |
| 交易所列 | `SH` / `SZ` / `BJ` |
| 溯源列 | 每行必有 `source`、`data_version`、`fetched_at`（UTC 时间戳） |
| 空值语义 | 停牌日：OHLCV 仍有值，`volume=0`、`amount=0` |
| 成交量单位 | A 股个股成交量一律 **股**；供应商报「手」的（TDX 日线、东财）由 adapter 在边界 ×100 |
| Schema 演进 | 只允许加列；破坏性变更须提升 `dataset_schema_version` |
| `data_version` | 语义变更（不是加列）才提升；见下「成交量单位」 |

### 分区键（curated）

| 数据集 | 分区 |
|---------|-----------|
| daily_bars | `trade_date`（按日） |
| index_bars | `trade_date`（按年） |
| minute_bars | `frequency`, `trade_date`, `symbol_bucket` |
| trading_status | `trade_date`（按月） |
| corporate_actions | `ex_date`（按年） |
| adj_factors | `trade_date`（按日） |
| financial_statement_items | `report_period` |
| industry_members | `as_of_date` |
| northbound_flows | `trade_date` |

多源快照路径：`meta/source_snapshots/{dataset}/source={source}/data_version={ver}/`

### 主键

| 数据集 | 主键 |
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
| financial_statement_items | `(symbol, report_period, statement_type, item_code, announce_date)` |
| industry_members | `(symbol, classification_system, as_of_date)` |

### MVP-P0 列定义

#### instruments

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | 主键 |
| name | string | |
| exchange | string | SH/SZ/BJ |
| asset_type | string | stock/etf/index |
| list_date | date | 可空 |
| delist_date | date | 可空 |
| prev_symbol | string | 可空 |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### trading_calendar

| 列 | 类型 | 说明 |
|--------|------|-------|
| trade_date | date | 主键 |
| is_trading | bool | |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### trading_status

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| is_trading | bool | |
| status | string | normal/suspended/st/*st |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### daily_bars

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| open | float64 | 未复权 |
| high | float64 | |
| low | float64 | |
| close | float64 | |
| volume | int64 | **股**（见下「成交量单位」）；`data_version=v2` 才保证 |
| amount | float64 | 人民币 |
| source | string | |
| data_version | string | `v2`=volume 为股；`v1`=按源而异，已弃用 |
| fetched_at | timestamp | |

##### 成交量单位（`daily_bars.volume`）

各家供应商的原生单位并不一致，而 payload 里没有任何字段声明它，所以混在一列里会**正好差 100 倍**——足以毁掉一切换手率/流动性因子，却小到行数、主键、OHLC 检查都发现不了。

**契约：一律存「股」。** 这也是唯一能让 `amount ≈ close × volume` 成立的选择，而这个恒等式正是质量检查赖以从数据本身发现单位错误的依据。每个 adapter 在自己的边界完成换算。

各源原生单位（比值 = `amount / close / volume`，全量 curated 实测；≈1 即为股，≈100 即为手）：

| source | 原生单位 | 证据 |
|--------|---------|------|
| tdx_protocol | 手 | 中位数 100.000，12,182,204 行 |
| ths | 股 | 中位数 0.999，5,303,037 行 |
| baostock | 股 | 中位数 1.000，374,888 行 |
| sina | 股 | 供应商口径；不提供 `amount`，比值无法实测 |
| eastmoney | 手 | **未独立验证**：`push2his` 在多数网络不可达，湖内东财行全是停牌占位零值；沿用 `commodity_bars` 已记录的「东财口径 = 手」（同一 endpoint 同一字段位）。若判断有误，`daily_bars_volume_unit` 会在第一批真实行落地时报错 |

TDX 的单位**按频率而非按源**：日线（`frequency=9`）是手，同一 wire parser 出来的 1 分钟线是股（实测 600519 1m bar vol=59,700，amount=88,977,784，价格 ~1490 → 59,716 股）。未来的 `minute_bars` 不得复用日线的换算，minute↔daily 的成交量对账必须股对股。

**质量检查。** `quality/unit_checks.py` 的 `daily_bars_volume_unit` 按 **source 分组**计算 `amount / close / volume` 中位数，落在 [0.8, 1.25] 之外即报 `error`（观测到的中位数与 1.0 相差不到 0.1%，容差留了 ~200 倍余量）。分组是刻意的：混单位的一列中位数既不接近 1 也不接近 100，且一个坏 adapter 会被另外几个健康的源在全市场口径下淹没。两个盲区已记录在案——sina 无 `amount` 因而不可测；`index_bars` / `sector_bars` 的 `close` 是点位不是股价，恒等式在那里没有意义（健康数据也会给出 36 的比值），故不在范围内。

**迁移（v1 → v2）。** 湖里既有的行在任何一种口径下都是错的，必须重写：

```bash
scripts/migrate_daily_bars_volume_v2.py --config configs/ashare-lake.toml --dry-run
scripts/migrate_daily_bars_volume_v2.py --config configs/ashare-lake.toml --apply
```

`source ∈ {tdx_protocol, sina}` 且 `data_version=v1` 的行 `volume ×100`；其余 v1 行原样保留（本就是股）；所有被处理的行改写为 `data_version=v2`。已是 v2 的行跳过，脚本幂等、可中断续跑。**`fetched_at` 不重新打戳**——这些行确实是当时抓的，改掉就抹掉了数据被观测到的时间；记录本次重新解释的列是 `data_version`，这正是它的用途。`--apply` 会就地改写 curated，请先备份。

#### index_bars

与 daily_bars 相同，另加 `frequency`（默认 `1d`）、`asset_type=index`。

**例外：`volume` 不是股。** index_bars / sector_bars 保留 TDX `index()` 调用返回的原值，未做换算——它与成分股加总在任何 100 的幂次上都对不上（000001.SH 实测：指数 amount 是沪市个股 amount 之和的 77%，但两边 volume 差约 300 倍，股/手两种读法都解释不了）。在这个单位被确证之前，按猜测缩放只会把断裂挪个地方。这两个数据集仍是 `data_version=v1`。

#### commodity_bars

国内商品期货**主力连续**日 K（东财主连）+ 窄口径外盘（新浪 COMEX 金 ``GC0.CMX``）。

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | 国内 `{根}0.{交易所}`（如 `AU0.SHF`）；外盘 `GC0.CMX`（COMEX 金连续） |
| name | string | 合约中文名 |
| exchange | string | `SHF` / `DCE` / `CZC` / `INE` / `GFE` / `CMX` |
| trade_date | date | 源交易所会话日（外盘为 COMEX 日历；与 A 股对齐在研究侧 as-of） |
| open/high/low/close | float64 | |
| volume | int64 | 手（东财口径；新浪外盘常为 0） |
| amount | float64 | 成交额（外盘可空） |
| open_interest | float64 | 可空 |
| source / data_version / fetched_at | | 溯源（`eastmoney` / `sina`） |

主键：`(symbol, trade_date)`。分区：`trade_date`。  
日更：`macro_risk` 组。历史：`asl backfill commodity_bars [--start 2020-01-01 --end …]`。  
`required=false`。外盘 v1 **仅黄金**；不进 A 股回测引擎。

#### corporate_actions

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| ex_date | date | |
| action_type | string | cash_dividend/bonus/transfer/allotment |
| cash_dividend | float64 | **每股**（元，税前） |
| bonus_ratio | float64 | **每股**（送股：每持有 1 股送出股数） |
| transfer_ratio | float64 | **每股**（转股：每持有 1 股转增股数） |
| allotment_ratio | float64 | **每股**（配股：每持有 1 股可配股数），可空 |
| allotment_price | float64 | 配股价（元/股），**不是**比率，可空 |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

> **单位契约（每股）。** 所有比率/金额均相对于「持有 1 股」，
> 不是通达信（`xdxr`）/东财常见的「每 10 股」口径。Adapter 在入 staging 前
> 把源侧「每 10 股」数值除以 10（例如「10 派 8.5 元」→ 0.85，「10 送 8 股」→ 0.8，
> 「10 转 4 股」→ 0.4，「10 配 3 股」→ 0.3）。下游按真实持股统一核算，无需再除 10：
> `shares_after = shares × (1 + bonus_ratio + transfer_ratio)`，
> `cash = shares × cash_dividend`。`allotment_price` 是每股价格而非比率，不做除 10。
> 注意：TDX `xdxr` 不拆分送/转，会把送转合计写入 `bonus_ratio`（`transfer_ratio=0`）；
> 总乘数正确，但送/转拆分仅在东财日更路径可区分。

#### adj_factors

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| adjust_type | string | qfq/hfq |
| factor | float64 | 累计因子；qfq：`1/sina_qfq_factor`，hfq：`sina_hfq_factor` |
| source | string | sina（默认） |
| data_version | string | |
| fetched_at | timestamp | |

#### financial_statement_items

时点（PIT）查询在读侧 **必须** 过滤 `announce_date <= as_of`
（`load(..., as_of=)`）；切勿仅按 `report_period` 对齐基本面。

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| report_period | string | 如 ``2024Q1`` |
| statement_type | string | income / balance / cashflow / indicator |
| item_code | string | 见下表 |
| item_value | float64 | 金额单位人民币元；比率类为百分数；每股类为元/股 |
| announce_date | date | **PIT 轴** — 首次披露日（取自业绩报表 `RPT_LICO_FN_CPD`） |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

**item_code 一览**（按 `statement_type`）：

| statement_type | item_code |
|----------------|-----------|
| income | `revenue` `operating_cost` `operating_profit` `total_profit` `net_profit` `net_profit_deducted` `income_tax` `sale_expense` `manage_expense` `finance_expense` |
| balance | `total_assets` `total_equity` `total_liabilities` `inventory` `accounts_receivable` `monetary_funds` `fixed_assets` |
| cashflow | `net_cash_operate` `net_cash_invest` `net_cash_finance` `capex` `end_cash` |
| indicator | `roe` `eps` `eps_deducted` `bps` `gross_margin` `ocf_per_share` `revenue_yoy` `net_profit_yoy` |

口径提醒：

- `total_equity` 是**股东权益合计**（含少数股东权益），不是归母净资产；做 B/P 时注意分子口径，
  或改用 `bps`（每股净资产）× 股本。
- `capex` 取「购建固定资产、无形资产和其他长期资产支付的现金」，是代理量而非严格资本开支。
- **回填值是修订后的**：东财只提供某期财务数据的*当前*版本。回填拿到的是修订值，
  但配的是首次披露日（statement 报表自带的 `NOTICE_DATE` 是「最后一次重述日」，
  往往晚 1–2 年，直接用会让基本面在 PIT 查询里整体迟到）。因此存在小幅前视：
  修订后的数字在首次披露日其实还不知道。只有日更逐日累积的版本才是严格 PIT。
- **历史深度**：`asl backfill financial_statement_items` 默认走东财报告期自 **2001** 起
  （可用 `--start` / `--end` 分块）；不走 baostock。盘上实际起点见 `list_datasets().coverage_start`。

#### fund_flow

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| main_net_inflow | float64 | 人民币 |
| super_large_net_inflow | float64 | |
| large_net_inflow | float64 | |
| medium_net_inflow | float64 | |
| small_net_inflow | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### margin_trading

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| margin_balance | float64 | |
| margin_buy | float64 | |
| short_balance | float64 | |
| short_sell_volume | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### northbound_holdings

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| channel | string | 沪/深股通 |
| holding_shares | float64 | |
| holding_mv | float64 | |
| holding_ratio | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### northbound_flows

| 列 | 类型 | 说明 |
|--------|------|-------|
| trade_date | date | |
| channel | string | SH / SZ |
| net_buy | float64 | |
| buy_amount | float64 | |
| sell_amount | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### valuation_metrics

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| pe_ttm | float64 | |
| pb | float64 | |
| ps_ttm | float64 | |
| total_mv | float64 | |
| float_mv | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### sector_members

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| sector_code | string | |
| sector_name | string | |
| as_of_date | date | 快照日 |
| source / data_version / fetched_at | | 溯源 |

#### announcement_index

PIT 查询过滤 `announce_date <= as_of`。

| 列 | 类型 | 说明 |
|--------|------|-------|
| announcement_id | string | 主键 |
| symbol | string | |
| title | string | |
| announce_date | date | **PIT 轴** |
| category | string | |
| url | string | |
| source / data_version / fetched_at | | 溯源 |

#### earnings_disclosure_schedule

预约披露时间表（EM datacenter `RPT_PUBLIC_BS_APPOIN`，镜像沪深交易所披露日历）。
现值语义、非 PIT：预约变更覆盖 `scheduled_date`，`first_scheduled_date` 保留首次预约，
`actual_date` 实际披露后回填（此前为 null）。

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| report_period | string | 如 ``2026Q2``（分区键） |
| scheduled_date | date | 当前有效预约披露日 |
| first_scheduled_date | date | 首次预约披露日 |
| actual_date | date | 实际披露日，未披露为 null |
| source / data_version / fetched_at | | 溯源 |

#### dragon_tiger

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| reason | string | |
| buy_amount | float64 | |
| sell_amount | float64 | |
| net_amount | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### block_trades

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| price | float64 | |
| volume | float64 | |
| amount | float64 | |
| premium_ratio | float64 | 相对收盘价折溢价 |
| source / data_version / fetched_at | | 溯源 |

#### index_constituents

| 列 | 类型 | 说明 |
|--------|------|-------|
| index_symbol | string | 如 ``000300.SH`` |
| symbol | string | 成分股 |
| as_of_date | date | 快照 / 调样日 |
| weight | float64 | 权重（百分比或比率，依源） |
| source / data_version / fetched_at | | 溯源 |

#### industry_members

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| classification_system | string | 如 ``sw``、``eastmoney`` |
| industry_code | string | |
| industry_name | string | |
| as_of_date | date | 分类快照日 |
| source / data_version / fetched_at | | 溯源 |

#### macro_indicators

| 列 | 类型 | 说明 |
|--------|------|-------|
| indicator_id | string | 如 ``shibor_3m``、``cnbond_yield_10y``、``lpr_1y`` |
| obs_date | date | 观测 / 发布日 |
| value | float64 | |
| frequency | string | ``daily`` / ``monthly`` |
| source / data_version / fetched_at | | 溯源 |

#### market_breadth

由 curated ``daily_bars`` 相对前一交易日计算。

| 列 | 类型 | 说明 |
|--------|------|-------|
| trade_date | date | |
| metric_id | string | ``advance_count``、``decline_count``、``limit_up_count`` 等 |
| value | float64 | |
| source / data_version / fetched_at | | 溯源 |

#### share_unlock_schedule

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| unlock_date | date | 计划解禁日 |
| unlock_shares | float64 | |
| unlock_ratio | float64 | 占流通/总股本比例（依源） |
| unlock_type | string | 如 IPO 限售、定向增发 |
| source / data_version / fetched_at | | 溯源 |

#### regulatory_events

| 列 | 类型 | 说明 |
|--------|------|-------|
| event_id | string | 主键 |
| symbol | string | |
| event_date | date | 公告日 |
| event_type | string | ``penalty``、``investigation``、``regulatory_letter`` 等 |
| title | string | |
| source / data_version / fetched_at | | 溯源 |

#### institutional_holdings

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| holder_type | string | ``fund``、``qfii``、``social_security`` 等 |
| report_period | string | 如 ``2024Q1`` |
| holding_shares | float64 | 持股数量或家数（依源） |
| holding_ratio | float64 | 占流通/总股本百分比 |
| holding_mv | float64 | 市值 |
| source / data_version / fetched_at | | 溯源 |

#### analyst_consensus

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| forecast_date | date | 发布 / 更新日期 |
| forecast_year | int64 | 目标财年 |
| eps_forecast | float64 | 一致预期 EPS |
| pe_forecast | float64 | 隐含 PE |
| target_price | float64 | 平均目标价 |
| rating | string | 如 买入/增持 |
| analyst_count | int64 | 覆盖机构数 |
| source / data_version / fetched_at | | 溯源 |

#### sentiment_scores

双通道：``announcement_keywords``（公告标题）与 ``stock_news_nlp``（东财个股新闻 + 关键词/SnowNLP）。

| 列 | 类型 | 说明 |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| score_channel | string | 主键维度；``announcement_keywords`` / ``stock_news_nlp`` |
| sentiment_score | float64 | [-1, 1] |
| headline_count | int64 | 计入评分的标题数 |
| source / data_version / fetched_at | | 溯源 |

#### stock_news（按需缓存）

缓存 JSON：``meta/on_demand/stock_news/{symbol}.json``；经 ``asl query --dataset stock_news --symbol`` 拉取。

| 字段 | 类型 | 说明 |
|-------|------|-------|
| symbol | string | |
| items[].news_id | string | |
| items[].title | string | |
| items[].publish_time | string | |
| items[].publish_date | string | 可解析时为 ISO 日期 |
| items[].sentiment_score | float64 | 单条 NLP 分 |
| items[].sentiment_method | string | ``keyword`` / ``snownlp`` / ``keyword+snownlp`` |
| aggregate_sentiment | float64 | 条目分数均值 |
| headline_count | int64 | |
| source / data_version / fetched_at | | 溯源 |

### Compact 去重

Compact 时按主键分组，保留 `fetched_at` 最大的一行。

### DuckDB 视图

优先用 `asl init` / compact 生成的 `{data_root}/duckdb/ashare-lake.duckdb` 视图，
不要手写整层 glob。`hive_partitioning=true` **仅**适用于按日分区的数据集
（目录值为 `YYYY-MM-DD`）；年/月分区必须 `hive_partitioning=false`（真实日期在文件列里）。
见 [lake-layout](../architecture/lake-layout.md)。

```sql
-- daily_bars / adj_factors 为按日分区，hive=true 安全
CREATE VIEW daily_bars_view AS
SELECT * FROM read_parquet('{root}/curated/daily_bars/**/*.parquet', hive_partitioning=true);

CREATE VIEW daily_bars_adj AS
SELECT b.*, b.close * a.factor AS adj_close
FROM daily_bars_view b
LEFT JOIN read_parquet('{root}/derived/adj_factors/**/*.parquet', hive_partitioning=true) a
  ON b.symbol = a.symbol AND b.trade_date = a.trade_date AND a.adjust_type = 'qfq';

-- 反例：index_bars 等按年分区时必须关掉 hive，否则目录 "1993" 会污染 DATE 列
-- SELECT * FROM read_parquet('.../index_bars/**/*.parquet', hive_partitioning=false);
```
