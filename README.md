# StockDataEngine

本地部署的 A 股数据层：多源采集、编排、标准化，落成带溯源、列契约稳定的 Parquet 数据湖。DuckDB / Polars 直接查，不用自建数据库，也不用通达信客户端。

CLI 是 `sde`，Python 包名 `stock_data_engine`。本仓库只做数据，不做回测和信号——那是下游的事。

<details>
<summary>English summary</summary>

Self-hosted **A-share data layer**: multi-source ingest, job orchestration, and a
versioned Parquet lake with provenance columns, DuckDB views, and a `load()` API
(`adjust` / `universe` / point-in-time `as_of`). Not a scraping SDK dump, not a
backtester. **Code is MIT; upstream market-data terms still apply** — see
[docs/legal-and-data-sources.md](docs/legal-and-data-sources.md).
How we differ from AkShare / Tushare / Baostock:
[docs/comparison.md](docs/comparison.md).

</details>

## 和同类项目差在哪

AkShare / efinance 之类能拉数，但通常不负责落盘契约、日更续跑和溯源列。Tushare Pro 有积分墙；Baostock / mootdx 单源 schema 各搞各的。Qlib / vn.py 往往带着整套研究平台。

这边做的是：多适配器进同一套主键/分区/`load()`，数据在你磁盘上的 curated Parquet。不伪造数据、可溯源、备源不自动顶替主源、财报按公告日做 PIT。更细的对照见 [docs/comparison.md](docs/comparison.md)。

## 有什么数据

按用途大致分层（字段与主键见 [schema](docs/datasets/schema.md)，速查表见 [catalog](docs/datasets/catalog.md)）：

- **基础**：instruments、交易日历、停复牌/ST
- **行情**：未复权日线、指数、复权因子
- **事件**：除权除息、公告索引、预约披露
- **基本面 / 估值**：财报科目、PE/PB 等、一致预期
- **资金**：北向、融资融券、主力流向、龙虎榜、机构持仓
- **结构**：板块/指数成分、行业分类
- **宏观与舆情**：利率货币指标、新闻与情绪分
- **风控类**：解禁、监管事件

已知限制（用之前看一眼）：`universe="all_a"` 的 ST/停牌过滤，只在 `trading_status` 有覆盖的日期生效——日更只抓当天，历史回填窗口之前不会按历史 ST 剔除。部分 HTTP 源在海外网络不稳定，大陆出口更稳。

## 安装

```bash
git clone https://github.com/rootSunc/stock-data-engine.git
cd stock-data-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"        # tdx = mootdx 行情源；开发另加 [dev]
```

可选 extras（`valuation` / `macro` / `nlp` / `structure`）见
[docs/getting-started/installation.md](docs/getting-started/installation.md)。

## 快速开始

```bash
cp configs/stockdata.example.toml configs/stockdata.toml   # 本地配置（已 gitignore）
sde init   --config configs/stockdata.toml                 # 建目录/manifest/视图 + 首次回填
sde run daily --config configs/stockdata.toml              # 每日增量
sde status --config configs/stockdata.toml
sde retry  --run-id <id> --config configs/stockdata.toml
```

首次全量 init 之后，建议按 [回填验收](docs/operations/runbook.md#回填完成验收) 过一遍幂等/口径/覆盖再挂 cron。

```bash
.venv/bin/python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# 同窗口重跑 daily 后再：
.venv/bin/python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

## 读数据

### Python API

```python
from stock_data_engine.query import load

bars = load(
    "daily_bars",
    start="2020-01-01", end="2025-12-31",
    adjust="hfq",              # None | "qfq" | "hfq"
    universe="all_a",          # 见上文「已知限制」
)

roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

### DuckDB

```bash
sde query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/stockdata.toml
```

也可直连 `{data_root}/duckdb/stockdata.duckdb`。

### 直读 Parquet

```python
import polars as pl
bars = pl.scan_parquet("data/stock-data-engine/curated/daily_bars/**/*.parquet")
df = bars.filter(pl.col("symbol") == "600519.SH").collect()
```

数据湖大致长这样：

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet
  derived/   adj_factors/...
  staging/   本次 run 原始落地（compact 后可清理）
  meta/      manifest、quality findings、水位、on-demand 缓存
  duckdb/    stockdata.duckdb
```

## 可信原则（简要）

1. 源失败就让 batch 失败，不静默塞假数（测试里 `allow_mock` 必须标 `source="mock"`）
2. 每行带 `source` / `data_version` / `fetched_at`
3. 日线存未复权价，复权因子单独存；qfq/hfq 在查询时组合
4. curated 每主键一行；备源进 snapshot，审计出 diff，不自动切主源
5. 财报等低频数据带 `announce_date`，用 `load(..., as_of=)` 做 PIT

## 文档

入口：[docs/README.md](docs/README.md)

| 分类 | 文档 |
|------|------|
| 定位 | [与同类项目差异](docs/comparison.md) · [许可与数据合规](docs/legal-and-data-sources.md) |
| 入门 | [安装](docs/getting-started/installation.md) · [快速开始](docs/getting-started/quickstart.md) · [配置](docs/getting-started/configuration.md) |
| 架构 | [总览](docs/architecture/overview.md) · [数据流](docs/architecture/data-flow.md) · [数据湖](docs/architecture/lake-layout.md) |
| 数据集 | [目录](docs/datasets/catalog.md) · [Schema](docs/datasets/schema.md) · [逐源限制](docs/datasets/sources.md) · [查询指南](docs/datasets/query-guide.md) |
| 运维 | [Runbook](docs/operations/runbook.md) · [故障排查](docs/operations/troubleshooting.md) |
| 开发 | [新增数据集](docs/development/adding-dataset.md) · [CONTRIBUTING](CONTRIBUTING.md) |
| 参考 | [CLI](docs/reference/cli.md) · [Python API](docs/reference/python-api.md) · [ADR](docs/adr/) · [CHANGELOG](CHANGELOG.md) |

## 许可与合规

- **代码**：[MIT](LICENSE)
- **数据**：落盘行情/公告仍受各上游条款约束；本仓库不附带数据湖，也不授予数据再分发权。见 [docs/legal-and-data-sources.md](docs/legal-and-data-sources.md)。
- **安全漏洞**：[SECURITY.md](SECURITY.md)
- **行为准则**：[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
