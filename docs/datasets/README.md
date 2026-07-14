# 数据集总览

StockDataEngine 交付 **26 个注册数据集**（25 curated + 1 derived `adj_factors`），按选股用途分为 L0–L8 八层。另有 **on-demand** 数据集不进 curated 主路径。

权威字段定义：[PRD 附录 A](../PRD.md)。逐源限制：[PRD 附录 B](../PRD.md)。

---

## 数据分层

| 层次 | 说明 | 代表数据集 |
|------|------|------------|
| **L0** 基础参考 | Universe、日历、交易状态 | instruments, trading_calendar, trading_status |
| **L1** 行情 | 未复权价量 + 复权因子 | daily_bars, index_bars, adj_factors |
| **L2** 公司事件 | 除权除息、公告 | corporate_actions, announcement_index |
| **L3** 基本面 | 财报、估值、一致预期 | financial_statement_items, valuation_metrics, analyst_consensus |
| **L4** 资金面 | 北向、融资、主力 | fund_flow, northbound_*, margin_trading, dragon_tiger, block_trades, institutional_holdings |
| **L5** 结构行业 | 板块、指数成分、行业 | sector_members, index_constituents, industry_members |
| **L6** 宏观 | 利率、景气、货币 | macro_indicators, market_breadth |
| **L7** 舆情 | 新闻、情绪 | sentiment_scores（stock_news 为 on-demand） |
| **L8** 风险合规 | 解禁、监管 | share_unlock_schedule, regulatory_events |

---

## Ingestion 模式

| 模式 | 含义 | 示例 |
|------|------|------|
| **batch** | 日更/周更，走 staging → compact → curated | daily_bars, fund_flow |
| **derived** | 由 curated 计算，可 `sde derive` 重算 | adj_factors |
| **on-demand** | 按 symbol 抓取，缓存于 meta | stock_news, announcement_body |

### fetch_semantics

| 值 | 行为 | 数据集示例 |
|----|------|------------|
| `by_date` | 可按日期回补缺口 | daily_bars, margin_trading |
| `snapshot` | 仅抓 run 当日快照，禁止伪造历史 | valuation_metrics, sector_members |

`snapshot` 数据集若配置了 `backfill_source`（如 `valuation_metrics` → baostock、`sector_bars` → eastmoney_kline），允许 `sde backfill` 走专用历史源。

---

## 溯源列（所有 curated 行）

| 列 | 类型 | 说明 |
|----|------|------|
| `source` | string | 数据源标识 |
| `data_version` | string | 源版本/批次 |
| `fetched_at` | timestamp[us, UTC] | 抓取时间 |

---

## PIT 数据集

带 `announce_date`，`load(..., as_of=)` 必填或强烈建议：

- `financial_statement_items`
- `announcement_index`

---

## On-Demand 数据集

配置于 `[on_demand].datasets`：

| 数据集 | 说明 |
|--------|------|
| `announcement_body` | 公告正文 |
| `stock_news` | 个股新闻 |
| `research_reports` | 研报 |
| `financial_reports` | 财报原文 |

访问：`sde query --dataset <name> --symbol <code>.SH`

---

## 注册表源码

单一事实来源：

- `domain/datasets.py` — `DatasetSpec`（分区、水位、语义、staleness）
- `domain/schemas.py` — Polars dtype、`PRIMARY_KEYS`

测试 `test_dataset_registry.py` 断言两者同步。

---

## 相关文档

- [数据集目录（全表）](catalog.md)
- [查询指南](query-guide.md)
- [steps 模块](../modules/steps.md)
