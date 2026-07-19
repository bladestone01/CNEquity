# ashare-lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.en.md)

本地跑的 A 股数据层：多源拉数、编排日更，落成带溯源列的 Parquet 湖。DuckDB / Polars 直接查，不用自建库，也不用通达信客户端。

CLI 叫 `asl`，包名 `ashare_lake`。仓库只做数据，回测和信号留给下游。

```
   tdx_protocol    eastmoney    sina    cninfo    baostock / akshare …
        │              │          │        │              │
        ▼              ▼          ▼        ▼              ▼
  ┌────────────────────────────────────────────────────────────┐
  │   asl run daily  ·  编排 / 水位 / 失败重试 / 质量审计         │
  └────────────────────────────────────────────────────────────┘
                              │
       staging ──▶ curated ──▶ derived        Parquet，行级带
                              │               source / data_version / fetched_at
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
  Python load() API      DuckDB 视图 / SQL     Polars 直读 Parquet
```

## 有什么数据

数据集名即 `load()` 的第一个参数。字段与主键见 [schema](docs/datasets/schema.md)，编排元数据速查见 [catalog](docs/datasets/catalog.md)。

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

## 安装

暂未发布 PyPI，当前从源码安装：

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"        # tdx = mootdx 行情源；开发另加 [dev]
```

用 uv 的话：`uv sync --extra tdx`（仓库带 `uv.lock`）。

可选 extras（`valuation` / `macro` / `nlp` / `structure`）见
[installation](docs/getting-started/installation.md)。

## 快速开始

```bash
cp configs/ashare-lake.example.toml configs/ashare-lake.toml   # 本地配置（已 gitignore）
asl init   --config configs/ashare-lake.toml                 # 建目录/manifest/视图 + 首次回填
asl run daily --config configs/ashare-lake.toml              # 每日增量
asl status --config configs/ashare-lake.toml
asl retry  --run-id <id> --config configs/ashare-lake.toml
```

全量 init 之后，建议按 [回填验收](docs/operations/runbook.md#回填完成验收) 过一遍幂等 / 口径 / 覆盖再挂 cron：

```bash
.venv/bin/python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# 同窗口重跑 daily 后再：
.venv/bin/python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

## 读数据

Python：

```python
from ashare_lake.query import load

bars = load(
    "daily_bars",
    start="2020-01-01", end="2025-12-31",
    adjust="hfq",              # None | "qfq" | "hfq"
    universe="all_a",          # 见下文「已知限制」
)

roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

DuckDB：

```bash
asl query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/ashare-lake.toml
```

也可直连 `{data_root}/duckdb/ashare-lake.duckdb`。

或者 Polars 直读 Parquet：

```python
import polars as pl
bars = pl.scan_parquet("data/ashare-lake/curated/daily_bars/**/*.parquet")
df = bars.filter(pl.col("symbol") == "600519.SH").collect()
```

湖目录大致是：

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet
  derived/   adj_factors/...
  staging/   本次 run 原始落地（compact 后可清理）
  meta/      manifest、quality findings、水位、on-demand 缓存
  duckdb/    ashare-lake.duckdb
```

## 设计取舍

- 源失败就 fail batch，不静默塞假数；测试里 `allow_mock` 必须标 `source="mock"`
- 行上带 `source` / `data_version` / `fetched_at`，问题数据可以追到来源和批次
- 日线存未复权价，复权因子单独存；qfq / hfq 在查询时组合
- curated 每主键一行；备源进 snapshot，audit 出 diff，不自动顶替主源
- 财报等低频数据带 `announce_date`，用 `load(..., as_of=)` 做 PIT，避免前视

## 已知限制

- `universe="all_a"` 的 ST / 停牌过滤只在 `trading_status` 有覆盖的日期生效——日更只抓当天，更早的历史窗口不会按历史 ST 剔除（可用 `asl backfill` 补历史）。
- 部分 HTTP 源在海外访问不稳定，大陆出口更稳；`sector_bars` 等历史回填需要国内网络或代理，见 [runbook](docs/operations/runbook.md)。
- 尚未发布 PyPI，目前只支持源码安装。

## 和同类项目的差异

AkShare / efinance 解决"怎么拉数"，这里解决的是拉完之后的事：多适配器进同一套主键 / 分区 / `load()` 契约，数据是你磁盘上可日更续跑、带溯源的 curated Parquet。Tushare Pro 有积分墙；Baostock / mootdx 单源、schema 互不兼容；Qlib / vn.py 则是整包研究平台。逐项对照见 [docs/comparison.md](docs/comparison.md)。

## 项目状态

当前 [0.1.0](CHANGELOG.md)，是作者自用数据层的首个公开版本，日常跑在自己的日更 cron 上。1.0 之前数据集名、schema、`load()` 签名可能有破坏性调整，变更都记录在 [CHANGELOG](CHANGELOG.md)。

这是个人项目：issue 和 PR 都欢迎，响应尽力而为。提 PR 前请看 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题走 [SECURITY.md](SECURITY.md)。

## 文档

入口在 [docs/README.md](docs/README.md)。常用的几份：

[和同类项目差异](docs/comparison.md) · [许可与数据合规](docs/legal-and-data-sources.md) ·
[安装](docs/getting-started/installation.md) · [快速开始](docs/getting-started/quickstart.md) ·
[配置](docs/getting-started/configuration.md) · [架构总览](docs/architecture/overview.md) ·
[数据集目录](docs/datasets/catalog.md) · [Schema](docs/datasets/schema.md) ·
[查询指南](docs/datasets/query-guide.md) · [Runbook](docs/operations/runbook.md) ·
[CLI](docs/reference/cli.md) · [Python API](docs/reference/python-api.md) ·
[ADR](docs/adr/) · [CHANGELOG](CHANGELOG.md)

## 许可

代码 [MIT](LICENSE)。落盘行情 / 公告仍受各上游条款约束；仓库不附带数据湖，也不授予数据再分发权——见 [legal](docs/legal-and-data-sources.md)。
