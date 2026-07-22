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

## 定位：与同类差异与设计取舍

AkShare / efinance 解决「怎么拉数」；本仓库解决拉完之后：多源进同一套主键 / 分区 / `load()` 契约，落成可日更续跑、带溯源的本地 curated Parquet。逐项说明见 [comparison](docs/comparison.md)。

| | ashare-lake | AkShare / efinance | Tushare Pro | Baostock / mootdx | Qlib / vn.py |
|--|-------------|-------------------|-------------|-------------------|--------------|
| 定位 | 自建数据湖 + 日更编排 | 拉数函数库 | 云端积分 API | 单源会话/协议 | 研究/交易平台 |
| 交付 | curated Parquet + `load()` | 内存 DataFrame | 远端表 | DataFrame | 平台内数据 |
| 编排 / 水位 / 重试 | 有 | 无 | 无 | 无 | 各平台自有 |
| Schema / 溯源 | 写前校验 + provenance | 通常无 | 平台字段 | 无湖契约 | 视模块 |
| 多源 | 主源进 curated；备源仅 snapshot | 单源调用 | 单平台 | 单源 | 视配置 |

| 取舍 | 选择 |
|------|------|
| 源失败 | fail batch，不静默塞假数；测试 `allow_mock` 须标 `source="mock"` |
| 溯源 | 每行 `source` / `data_version` / `fetched_at` |
| 复权 | 日线存未复权；因子单独存；`qfq` / `hfq` 查询时组合 |
| 主备 | curated 每主键一行；备源进 snapshot，audit 出 diff，不自动顶替 |
| 前视 | 财报等带 `announce_date`，`load(..., as_of=)` 做 PIT |

## 已知限制

- **幸存者偏差**：`instruments` / `daily_bars` 的历史是按*当前*上市名单回填的，退市股不在湖里
  （audit 检查 `universe_survivorship_absent` 会报错）。A 股退市前普遍已跌 80%+，缺了它们的
  回测收益会系统性偏高，且偏差集中在小盘 / 低估值这些因子最爱买的桶里。补齐前所有收益序列都要打折看。
- `universe="all_a"` 的 ST / 停牌过滤只在 `trading_status` 有覆盖的日期生效——日更只抓当天，更早的历史窗口不会按历史 ST 剔除（可用 `asl backfill` 补历史）。
- 北交所行情走 Sina 而非 TDX（TDX 协议不提供北交所）；BJ 行的 `amount` 为 null，
  新上市的 BJ 票需 `asl delisted discover` 扫到后才进 `instruments`。
- 部分 HTTP 源在海外访问不稳定，大陆出口更稳；`sector_bars` 等历史回填需要国内网络或代理，见 [runbook](docs/operations/runbook.md)。
- 尚未发布 PyPI，目前只支持源码安装。

## 项目状态

当前 [0.1.0](CHANGELOG.md)，是作者自用数据层的首个公开版本，日常跑在自己的日更 cron 上。1.0 之前数据集名、schema、`load()` 签名可能有破坏性调整，变更都记录在 [CHANGELOG](CHANGELOG.md)。

这是个人项目：issue 和 PR 都欢迎，响应尽力而为。提 PR 前请看 [贡献指南](CONTRIBUTING.md)；安全问题走 [安全策略](SECURITY.md)。文档以中文为主；[CHANGELOG](CHANGELOG.md) 与 [ADR](docs/adr/) 为英文。

## 文档

入口在 [docs/README.md](docs/README.md)。常用的几份：

[定位对照](docs/comparison.md) · [许可与数据合规](docs/legal-and-data-sources.md) ·
[安装](docs/getting-started/installation.md) · [快速开始](docs/getting-started/quickstart.md) ·
[配置](docs/getting-started/configuration.md) · [架构总览](docs/architecture/overview.md) ·
[数据集目录](docs/datasets/catalog.md) · [Schema](docs/datasets/schema.md) ·
[查询指南](docs/datasets/query-guide.md) · [Runbook](docs/operations/runbook.md) ·
[CLI](docs/reference/cli.md) · [Python API](docs/reference/python-api.md) ·

## 许可

代码 [MIT](LICENSE)。落盘行情 / 公告仍受各上游条款约束；仓库不附带数据湖，也不授予数据再分发权——见 [legal](docs/legal-and-data-sources.md)。
