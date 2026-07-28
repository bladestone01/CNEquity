# A股数据湖

# 本地可日更、可溯源的金融数据研究底座

多源拉数 → 日更编排 → 带溯源的 curated Parquet。  
DuckDB / Polars / Python `load()` 直接查，不用自建库，也不用通达信客户端。

CLI：`asl` · 包名：`ashare_lake` · **只做数据层**（回测和信号留给下游）。

## 为什么用它

AkShare / efinance 解决「怎么拉数」；Tushare 解决「云端宽表」；Qlib / vn.py 解决「研究/交易平台」。  
**ashare-lake** 专做中间层：多源进同一契约，落成可日更、可溯源、可审计的本地 Parquet 湖。

相对同类更突出的几点：

- **本地可续跑**：水位 / 失败重试 / 质量审计，适合挂 cron
- **行级溯源**：每行带 `source` / `data_version` / `fetched_at`，出了问题能查回来源
- **多源可核验**：主源进 curated，备源进 snapshot，可 diff，不静默顶替
- **研究口径稳定**：`load()` 统一复权 / universe / PIT `as_of`，不用自己拼
- **纯数据层**：不做回测和交易，和拉数库互补、也不绑架整套框架

一条命令跑通真数 demo：

## 安装与一分钟体验

```bash
pip install ashare-lake
asl demo
```

通过 TDX 拉几只流动性股票 × 约 30 个交易日，写入 `data/ashare-lake-demo/`，并打印样例表。需要能访问 TDX 行情主机（大陆出口更稳）。

```bash
asl query --config configs/ashare-lake.demo.toml --sql "
  SELECT symbol, trade_date, close, volume, source
  FROM daily_bars
  WHERE symbol = '600519.SH'
  ORDER BY trade_date DESC
  LIMIT 10
"
```

全量日更（仍不必 clone；在含配置的工作目录执行）：

```bash
asl config init                              # → configs/ashare-lake.toml
# 按需编辑 data.root，例如：
# asl config init --data-root /data/ashare-lake --force
asl config validate --config configs/ashare-lake.toml
asl init --config configs/ashare-lake.toml
asl run daily --config configs/ashare-lake.toml
```

<p align="center">
  <img src="https://raw.githubusercontent.com/rootSunc/ashare-lake/main/docs/assets/asl-demo.png" alt="asl demo" width="820" />
</p>

## 有什么数据

数据集名即 `load()` 的第一个参数。字段见 [schema](https://github.com/rootSunc/ashare-lake/blob/main/docs/datasets/schema.md)，编排元数据见 [catalog](https://github.com/rootSunc/ashare-lake/blob/main/docs/datasets/catalog.md)。

| 类别 | 数据集 |
|------|--------|
| 基础参考 | `instruments` · `trading_calendar` · `trading_status`（停复牌 / ST） |
| 行情 | `daily_bars`（未复权） · `index_bars` · `adj_factors` |
| 公司事件 | `corporate_actions` · `announcement_index` · `earnings_disclosure_schedule` |
| 基本面 / 估值 | `financial_statement_items`（PIT） · `valuation_metrics` · `analyst_consensus` |
| 资金面 | `fund_flow` · `margin_trading` · `northbound_flows` / `northbound_holdings` · `dragon_tiger` · `block_trades` · `institutional_holdings` |
| 结构 / 行业 | `sector_members` · `index_constituents` · `industry_members` |
| 宏观 | `macro_indicators` · `market_breadth` |
| 舆情 / 轮动 | `sentiment_scores` · `hot_rank` · `sector_bars` · `sector_fund_flow` · `news_headlines` |
| 风险 | `share_unlock_schedule` · `regulatory_events` |

## 读数据

```python
from ashare_lake.query import load

bars = load("daily_bars", start="2020-01-01", end="2025-12-31", adjust="hfq")
roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

无 extras —— `pip install ashare-lake` 即装齐所有数据源。

## 完整文档

详细 schema、runbook、定位对照与合规说明以 GitHub 为准：

- [仓库](https://github.com/rootSunc/ashare-lake)
- [文档](https://github.com/rootSunc/ashare-lake/tree/main/docs)
- [Changelog](https://github.com/rootSunc/ashare-lake/blob/main/CHANGELOG.md)

代码 MIT。落盘行情 / 公告仍受上游条款约束——本包不附带、也不再分发数据湖。
