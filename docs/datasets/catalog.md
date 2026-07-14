# 数据集目录

完整字段见 [PRD 附录 A](../PRD.md)。下表为编排与查询元数据速查。

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
| daily_bars | trade_date | symbol, trade_date | by_date | ✓ | tdx_protocol | EM failover snapshot |
| index_bars | trade_date | symbol, trade_date, frequency | by_date | ✓ | tdx_protocol | |
| adj_factors | trade_date | symbol, trade_date, adjust_type | derived | ✓ | sina | 仅 hfq；`sde derive adj_factors` |

---

## L2 公司事件

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| corporate_actions | ex_date | symbol, ex_date, action_type | by_date | ✓ | eastmoney (daily) | backfill: tdx_protocol |
| announcement_index | announce_date | announcement_id | by_date PIT | ✓ | cninfo | `as_of` 过滤 |

---

## L3 基本面

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 | 备注 |
|--------|--------|------|------|------|------|------|
| financial_statement_items | report_period | symbol, report_period, statement_type, item_code | by_date PIT | — | eastmoney | 按报告期分区 |
| valuation_metrics | trade_date | symbol, trade_date | snapshot | ✓ | eastmoney | backfill: baostock |
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

---

## L6 宏观

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 |
|--------|--------|------|------|------|------|
| macro_indicators | obs_date | indicator_id, obs_date | by_date | ✓ | eastmoney / akshare |
| market_breadth | trade_date | trade_date, metric_id | by_date | ✓ | derived (daily_bars) |

---

## L7 舆情

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 |
|--------|--------|------|------|------|------|
| sentiment_scores | trade_date | symbol, trade_date, score_channel | by_date | ✓ | derived |

---

## L8 风险合规

| 数据集 | 分区键 | 主键 | 语义 | 水位 | 主源 |
|--------|--------|------|------|------|------|
| share_unlock_schedule | unlock_date | symbol, unlock_date | by_date | ✓ | eastmoney |
| regulatory_events | event_date | event_id | by_date | ✓ | cninfo |

---

## Failover 配置（meta/source_snapshots）

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
| events.py | corporate_actions, announcement_index |
| fundamentals.py | valuation_metrics, financial_statement_items |
| capital.py | fund_flow, northbound_*, margin_trading, dragon_tiger, block_trades |
| structure.py | sector_members, index_constituents, industry_members |
| macro_risk.py | macro_indicators, market_breadth, share_unlock_schedule, regulatory_events |
| research.py | institutional_holdings, analyst_consensus, sentiment_scores |
| finalize.py | compact, derive_adj_factors, audit |

---

## 相关文档

- [查询指南](query-guide.md)
- [PRD 附录 B 数据源限制](../PRD.md)
