# 数据集目录

完整字段见 [schema.md](schema.md)。下表为编排与查询元数据速查。

**图例**：语义 `by_date` / `snapshot`；水位 ✓ = 维护 `meta/state` 水位。

---

## L0 基础参考

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| instruments | —（单文件 merge） | symbol | by_date | — | tdx_protocol | EM 补 list_date；merge 保留退市 |
| trading_calendar | trade_date | trade_date | by_date | ✓ | exchange_calendar | 种子 CSV 2016–2027 |
| trading_status | trade_date | symbol, trade_date | by_date | ✓ | eastmoney | + akshare ST；baostock ST 回填；派生停牌 |

---

## L1 行情

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| daily_bars | trade_date | symbol, trade_date | by_date | ✓ | tdx_protocol | 东财备源快照（failover） |
| index_bars | trade_date | symbol, trade_date, frequency | by_date | ✓ | tdx_protocol | |
| commodity_bars | trade_date | symbol, trade_date | by_date | ✓ | eastmoney+sina | 国内主连 + COMEX金 `GC0.CMX`；`asl backfill commodity_bars`；required=false |
| adj_factors | trade_date | symbol, trade_date, adjust_type | derived | ✓ | sina | 仅 hfq；`asl derive adj_factors` |

---

## L2 公司事件

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| corporate_actions | ex_date | symbol, ex_date, action_type | by_date | ✓ | eastmoney（日更） | 回填：tdx_protocol |
| announcement_index | announce_date | announcement_id | by_date PIT | ✓ | cninfo | `as_of` 过滤 |
| earnings_disclosure_schedule | report_period | symbol, report_period | by_date | — | eastmoney | 预约披露时间表（RPT_PUBLIC_BS_APPOIN）；现值语义非 PIT：变更覆盖 scheduled_date（first_scheduled_date 保留首约，actual_date 披露后回填）；`asl backfill` 走 2016 起全报告期 |

---

## L3 基本面

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| financial_statement_items | report_period | symbol, report_period, statement_type, item_code | by_date PIT | — | eastmoney | 按报告期分区 |
| valuation_metrics | trade_date | symbol, trade_date | snapshot | ✓ | eastmoney | 回填：baostock |
| analyst_consensus | forecast_date | symbol, forecast_date | snapshot | ✓ | eastmoney | |

---

## L4 资金面

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | staleness |
|--------|--------|------|------|------|------|-----------|
| fund_flow | trade_date | symbol, trade_date | snapshot | ✓ | eastmoney | 1d |
| margin_trading | trade_date | symbol, trade_date | by_date | ✓ | eastmoney | 2d |
| northbound_holdings | trade_date | symbol, trade_date, channel | by_date | ✓ | eastmoney | 100d（季频） |
| northbound_flows | trade_date | trade_date, channel | by_date | ✓ | eastmoney | 2d |
| dragon_tiger | trade_date | symbol, trade_date, reason | by_date | ✓ | eastmoney | 1d |
| block_trades | trade_date | symbol, trade_date, price, volume | by_date | ✓ | eastmoney | 1d |
| institutional_holdings | report_period | symbol, holder_type, report_period | by_date | — | eastmoney | |

---

## L5 结构行业

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 |
|--------|--------|------|------|------|------|
| sector_members | as_of_date | symbol, sector_code, as_of_date | snapshot | ✓ | eastmoney |
| index_constituents | as_of_date | index_symbol, symbol, as_of_date | snapshot | ✓ | eastmoney |
| industry_members | as_of_date | symbol, classification_system, as_of_date | snapshot | ✓ | eastmoney |

快照类仅积累「每日一份成员关系」，历史分位数需多日分区累积。

历史回填（C2）：`asl backfill industry_members` = 申万 SwClass2021 月度（`classification_system=sw`，2020 起）；
`asl backfill index_constituents` = 国证调样史（399001/399006，约 2021-12 起）。中证 000300/000905 仍仅日更 EM 快照。

---

## L6 宏观

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 |
|--------|--------|------|------|------|------|
| macro_indicators | obs_date | indicator_id, obs_date | by_date | ✓ | eastmoney / akshare |
| market_breadth | trade_date | trade_date, metric_id | by_date | ✓ | derived (daily_bars) |

---

## L7 舆情 / 轮动

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| sentiment_scores | trade_date | symbol, trade_date, score_channel | by_date | ✓ | derived | |
| hot_rank | trade_date | symbol, trade_date | snapshot | ✓ | eastmoney | 人气榜 top500 |
| sector_bars | trade_date | sector_code, trade_date | snapshot | ✓ | eastmoney | 回填：eastmoney_kline（push2his） |
| sector_fund_flow | trade_date | sector_code, trade_date | snapshot | ✓ | eastmoney | 板块主力净流入 |
| news_headlines | publish_date | news_id | snapshot | ✓ | eastmoney | 7×24 快讯 |

`sector_bars` 日更只有当日 OHLC；历史由 `asl backfill sector_bars` 一次性写入（国内网络或代理）。
海外一键脚本见引擎 `scripts/china_egress_backfill.sh`（含 `trading_status` ST 回填）。

---

## L8 风险合规

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 |
|--------|--------|------|------|------|------|
| share_unlock_schedule | unlock_date | symbol, unlock_date | by_date | ✓ | eastmoney |
| regulatory_events | event_date | event_id | by_date | ✓ | cninfo |

---

## 主备配置（Failover → meta/source_snapshots）

| 数据集 | 主源 | 备源 |
|--------|------|------|
| daily_bars | tdx_protocol | eastmoney |
| corporate_actions | eastmoney | tdx_protocol |

---

## Step → 数据集映射

| Step 模块 | 数据集 |
|-----------|--------|
| reference.py | instruments, trading_calendar, trading_status |
| bars.py | daily_bars, index_bars |
| events.py | corporate_actions, announcement_index, earnings_disclosure_schedule |
| fundamentals.py | valuation_metrics, financial_statement_items |
| capital.py | fund_flow, northbound_*, margin_trading, dragon_tiger, block_trades |
| structure.py | sector_members, index_constituents, industry_members |
| macro_risk.py | macro_indicators, market_breadth, share_unlock_schedule, regulatory_events |
| commodity.py | commodity_bars |
| research.py | institutional_holdings, analyst_consensus, sentiment_scores |
| rotation.py | hot_rank, sector_bars, sector_fund_flow, news_headlines |
| finalize.py | compact, derive_adj_factors, audit |

---

## 相关文档

- [查询指南](query-guide.md)
- [逐源限制](sources.md)
