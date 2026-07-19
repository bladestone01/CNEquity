# ashare-lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

本地跑的 A 股数据层：多源拉数、编排日更，落成带溯源列的 Parquet 湖。DuckDB / Polars 直接查就行，不用自建库，也不用通达信客户端。

CLI 叫 `asl`，包名 `ashare_lake`。仓库只做数据，回测和信号留给下游。

<details>
<summary>English summary</summary>

Self-hosted A-share data layer: multi-source ingest, job orchestration, and a
versioned Parquet lake with provenance columns, DuckDB views, and a `load()` API
(`adjust` / `universe` / point-in-time `as_of`). Not a scraping SDK dump, not a
backtester. Code is MIT; upstream market-data terms still apply — see
[docs/legal-and-data-sources.md](docs/legal-and-data-sources.md).
How we differ from AkShare / Tushare / Baostock:
[docs/comparison.md](docs/comparison.md).

</details>

## 和同类项目差在哪

AkShare / efinance 之类能拉数，但通常不管落盘契约、日更续跑和溯源列。Tushare Pro 有积分墙；Baostock / mootdx 单源 schema 各搞各的。Qlib / vn.py 往往整包研究平台一起上。

这边是多适配器进同一套主键 / 分区 / `load()`，数据在你磁盘上的 curated Parquet。源挂了就让 batch 失败，不静默塞假数；备源进 snapshot 做比对，不自动顶替主源；财报按公告日做 PIT。更细的对照见 [docs/comparison.md](docs/comparison.md)。

## 有什么数据

字段与主键见 [schema](docs/datasets/schema.md)，速查见 [catalog](docs/datasets/catalog.md)。大致包括：

- 基础：instruments、交易日历、停复牌 / ST
- 行情：未复权日线、指数、复权因子
- 事件：除权除息、公告索引、预约披露
- 基本面 / 估值：财报科目、PE/PB、一致预期
- 资金：北向、两融、主力流向、龙虎榜、机构持仓
- 结构：板块 / 指数成分、行业分类
- 宏观与舆情：利率货币、新闻与情绪分
- 风控类：解禁、监管事件

用之前注意：`universe="all_a"` 的 ST / 停牌过滤，只在 `trading_status` 有覆盖的日期生效——日更只抓当天，更早的历史窗口不会按历史 ST 剔除。部分 HTTP 源在海外不稳定，大陆出口更稳。

## 安装

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"        # tdx = mootdx 行情源；开发另加 [dev]
```

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

全量 init 之后，建议按 [回填验收](docs/operations/runbook.md#回填完成验收) 过一遍幂等 / 口径 / 覆盖再挂 cron。

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
    universe="all_a",          # 见上文「已知限制」
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

几点实现上的取舍（别当口号，就是代码现状）：

- 源失败就 fail batch；测试里 `allow_mock` 必须标 `source="mock"`
- 行上带 `source` / `data_version` / `fetched_at`
- 日线存未复权价，复权因子单独存；qfq / hfq 在查询时组合
- curated 每主键一行；备源进 snapshot，audit 出 diff，不自动切主源
- 财报等低频数据带 `announce_date`，用 `load(..., as_of=)` 做 PIT

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

代码 [MIT](LICENSE)。落盘行情 / 公告仍受各上游条款约束；仓库不附带数据湖，也不授予数据再分发权——见 [legal](docs/legal-and-data-sources.md)。安全问题见 [SECURITY.md](SECURITY.md)。
