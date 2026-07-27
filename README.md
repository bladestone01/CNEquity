# ashare-lake

[![CI](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml/badge.svg)](https://github.com/rootSunc/ashare-lake/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/rootSunc/ashare-lake/graph/badge.svg)](https://codecov.io/gh/rootSunc/ashare-lake)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.en.md)

**本地 A 股数据湖**：多源拉数 → 日更编排 → 带溯源的 curated Parquet。  
DuckDB / Polars / `load()` 直接查，不用自建库，也不用通达信客户端。

CLI：`asl` · 包名：`ashare_lake` · **只做数据层**，回测和信号留给下游。

### 为什么用它

- ✅ **一分钟真数**：`asl demo` 拉真实行情（TDX），不是假数据玩具
- ✅ **可续跑的日更**：水位 / 失败重试 / 质量审计，适合挂 cron
- ✅ **行级溯源**：每行带 `source` / `data_version` / `fetched_at`
- ✅ **统一契约**：多源进同一套主键、分区、`load()` API
- ✅ **查询零摩擦**：Python `load()` · DuckDB SQL · Polars 直读 Parquet
- ✅ **不做回测框架**：专做「拉完数之后」——和 AkShare / efinance 互补

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

## 一分钟体验

不用全市场回填。装好后一条命令拉 **5 只流动性股票 × 约 30 个交易日** 的真实行情：

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"
asl demo
```

<p align="center">
  <img src="docs/assets/asl-demo.png" alt="asl demo：分阶段拉数并打印样例日线" width="820" />
</p>

数据在独立目录 `data/ashare-lake-demo/`（不会污染之后的全量 `asl init`）。接着查：

```bash
asl query --config configs/ashare-lake.demo.toml --sql "
  SELECT symbol, trade_date, close, volume, source
  FROM daily_bars
  WHERE symbol = '600519.SH'
  ORDER BY trade_date DESC
  LIMIT 10
"
```

<p align="center">
  <img src="docs/assets/asl-query.png" alt="asl query：带 source 溯源列的日线" width="720" />
</p>

可选：`asl demo --symbols 600519.SH,000001.SZ --days 10`。需要能访问 TDX 行情主机（大陆出口更稳）；失败先看 `asl servers test`。

## 定位：与同类差异

AkShare / efinance 解决「怎么拉数」；本仓库解决拉完之后：多源进同一套主键 / 分区 / `load()` 契约，落成可日更续跑、带溯源的本地 curated Parquet。细节见 [comparison](docs/comparison.md)。

| | ashare-lake | AkShare / efinance | Tushare Pro | Baostock / mootdx | Qlib / vn.py |
|--|-------------|-------------------|-------------|-------------------|--------------|
| 定位 | 自建数据湖 + 日更编排 | 拉数函数库 | 云端积分 API | 单源会话/协议 | 研究/交易平台 |
| 交付 | curated Parquet + `load()` | 内存 DataFrame | 远端表 | DataFrame | 平台内数据 |
| 编排 / 水位 / 重试 | 有 | 无 | 无 | 无 | 各平台自有 |
| Schema / 溯源 | 写前校验 + provenance | 通常无 | 平台字段 | 无湖契约 | 视模块 |
| 多源 | 主源进 curated；备源仅 snapshot | 单源调用 | 单平台 | 单源 | 视配置 |

**设计取舍（摘要）**：源失败 fail batch（不静默假数）· 日线未复权、因子另存 · 备源不自动顶替 curated · 财报带 `announce_date` 做 PIT。完整取舍见 [comparison](docs/comparison.md)。

## 有什么数据

数据集名即 `load()` 的第一个参数。字段见 [schema](docs/datasets/schema.md)，编排元数据见 [catalog](docs/datasets/catalog.md)。

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

## 安装与日更

暂未发布 PyPI。装好环境后（与上面 demo 相同）：

```bash
pip install -e ".[tdx]"          # 开发另加 [dev]；也可用 uv sync --extra tdx
cp configs/ashare-lake.example.toml configs/ashare-lake.toml
asl init   --config configs/ashare-lake.toml    # 建目录/manifest/视图 + 首次回填
asl run daily --config configs/ashare-lake.toml # 每日增量
asl status --config configs/ashare-lake.toml
```

可选 extras（`valuation` / `macro` / `nlp` / `structure`）见 [installation](docs/getting-started/installation.md)。  
全量回填后建议按 [回填验收](docs/operations/runbook.md#回填完成验收) 再挂 cron。

## 读数据

```python
from ashare_lake.query import load

bars = load(
    "daily_bars",
    start="2020-01-01", end="2025-12-31",
    adjust="hfq",              # None | "qfq" | "hfq"
    universe="all_a",
)
roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

<p align="center">
  <img src="docs/assets/asl-load.png" alt="Python load()：从本地 curated Parquet 读日线" width="720" />
</p>

```bash
asl query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/ashare-lake.toml
```

也可直连 `{data_root}/duckdb/ashare-lake.duckdb`，或对按日分区的数据集用 Polars `scan_parquet`。  
年/月分区（如 `index_bars`）请优先 `asl query` / `load()`，避免 hive 分区标签撞真日期——见 [lake-layout](docs/architecture/lake-layout.md)。

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet
  derived/   adj_factors/...
  staging/   本次 run 原始落地（compact 后可清理）
  meta/      manifest、quality findings、水位、on-demand 缓存
  duckdb/    ashare-lake.duckdb
```

## 已知限制

- **幸存者偏差**：退市股需 `asl delisted backfill` + `repair`；未补齐前收益序列要打折看
- **海外网络**：部分 HTTP / 板块回填依赖大陆出口；行情 demo 需 TDX 可达
- **未上 PyPI**：目前仅源码安装

更多（ST 历史过滤、北交所、分区陷阱）见 [runbook](docs/operations/runbook.md) 与 [legal](docs/legal-and-data-sources.md)。

## 项目状态

[0.1.0](CHANGELOG.md) — 作者自用数据层的首个公开版，日常挂 cron。1.0 前 schema / `load()` 可能有破坏性调整，见 [CHANGELOG](CHANGELOG.md)。

个人项目：issue / PR 欢迎，响应尽力而为。[贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md)。文档中文为主；[CHANGELOG](CHANGELOG.md) 与 [ADR](docs/adr/) 为英文。

## 文档

[docs/README.md](docs/README.md) · [定位对照](docs/comparison.md) · [安装](docs/getting-started/installation.md) · [快速开始](docs/getting-started/quickstart.md) · [配置](docs/getting-started/configuration.md) · [架构](docs/architecture/overview.md) · [数据集](docs/datasets/catalog.md) · [Schema](docs/datasets/schema.md) · [查询](docs/datasets/query-guide.md) · [Runbook](docs/operations/runbook.md) · [CLI](docs/reference/cli.md) · [Python API](docs/reference/python-api.md)

## 许可

代码 [MIT](LICENSE)。落盘行情 / 公告仍受上游条款约束；仓库不附带数据湖，也不授予再分发权——见 [legal](docs/legal-and-data-sources.md)。
