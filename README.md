# StockDataEngine

本地部署的 A 股选股数据层：多源采集、编排、标准化，交付**带溯源、列契约稳定的 Parquet 数据湖**，DuckDB / Polars 直接查询，无需自建数据库、无需通达信客户端。

- **CLI**：`sde`　**Python 包**：`stock_data_engine`
- **交付物**：curated Parquet 分区数据湖 + DuckDB 视图 + Python 读取 API
- **定位**：下游选股/因子项目的唯一数据源；本引擎只管数据，不做回测与信号

> 状态标注：✅ 已可用　🚧 开发中　🔜 规划中（详见[路线图](#路线图可实施步骤)）。
> 使用方式按**终态**描述，未标注的示例即当前已可用。

---

## 数据全景（按选股用途分层）

| 层次 | 内容 | 选股用途 | 代表数据集 | 状态 |
|------|------|----------|------------|------|
| L0 基础参考 | Universe、交易日历、停复牌/ST | 可交易过滤、回测窗口 | instruments, trading_calendar, trading_status | 🚧 M2 |
| L1 行情 | 未复权日线 + 复权因子 | 动量、波动、量价因子 | daily_bars, index_bars, adj_factors | 🚧 M2 |
| L2 公司事件 | 除权除息、公告索引 | 事件驱动、除权回补 | corporate_actions, announcement_index | 🚧 M2/M3 |
| L3 基本面 | 财报科目、估值、一致预期 | 价值/质量/成长因子 | financial_statement_items, valuation_metrics | 🔜 M3+ |
| L4 资金面 | 北向、融资、主力、龙虎榜 | 聪明钱、杠杆情绪 | fund_flow, northbound_*, margin_trading, dragon_tiger | 🔜 M3 |
| L5 结构行业 | 板块、指数成分、行业分类 | 行业中性、板块轮动 | sector_members, index_constituents, industry_members | 🔜 M3+ |
| L6 宏观 | 利率、货币、景气指标 | 宏观择时、风格切换 | macro_indicators | 🔜 v1.1 |
| L7 舆情文本 | 新闻、研报、情绪得分 | NLP 特征、事件挖掘 | stock_news, research_reports, sentiment_scores | 🔜 v1.1 |
| L8 风险合规 | 解禁、监管处罚 | 负面清单、风控过滤 | share_unlock_schedule, regulatory_events | 🔜 v1.1 |

完整字段契约、主键、分区键与逐源限制见 [docs/PRD.md](docs/PRD.md)（附录 A/B）。

## 数据可信原则

1. **永不伪造**：数据源失败即判 batch failed，绝不静默返回假数据（mock 仅限测试开关 `allow_mock`，且强制标记 `source="mock"`，审计自动拦截）。✅
2. **可溯源**：每行 curated 数据带 `source / data_version / fetched_at` 三列。✅
3. **口径可重算**：日线存**未复权**价 + 独立复权因子，qfq/hfq 在查询期组合，不污染原始数据。✅
4. **多源不打架**：curated 每主键一行 canonical；备源进 snapshot，审计出 diff，永不自动切源。🔜 M4
5. **无前视偏差**：财报等低频数据带公告日 `announce_date` 双时间轴，按「截至当日已公告」对齐。🔜 M3+

---

## 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"        # tdx = mootdx 行情源；开发另加 [dev]
```

## 快速开始

```bash
cp configs/stockdata.example.toml configs/stockdata.toml   # 本地配置（已 gitignore）
sde init   --config configs/stockdata.toml                 # 建目录/manifest/视图 + 首次回填
sde run daily --config configs/stockdata.toml              # 每日增量（可挂 cron，见 PRD 附录 C）
sde status --config configs/stockdata.toml                 # 运行状态与失败批次
sde retry  --run-id <id> --config configs/stockdata.toml   # 只重跑失败部分
```

## 使用数据（三种方式，任选）

### 1. Python API（推荐，🔜 Phase 2）

选股/因子项目 `import` 即用，复权、Universe 过滤、PIT 对齐等口径逻辑内置：

```python
from stock_data_engine.query import load

# 全市场后复权日线，自动剔除 ST/停牌/未上市
bars = load(
    "daily_bars",
    start="2020-01-01", end="2025-12-31",
    adjust="hfq",              # None | "qfq" | "hfq"，查询期组合复权因子
    universe="all_a",          # 应用 instruments + trading_status 过滤
)

# 财报科目，按「2024-04-30 当日已公告」的口径取数（无前视偏差）
roe = load("financial_statement_items", items=["roe"], as_of="2024-04-30")
```

### 2. DuckDB SQL（✅ 已可用）

```bash
sde query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/stockdata.toml
```

或任何支持 DuckDB 的工具直连 `{data_root}/duckdb/stockdata.duckdb`（内置 `daily_bars`、`daily_bars_adj`、`instruments`、`adj_factors` 等视图）。

### 3. 直读 Parquet（✅ 已可用，零依赖本项目）

数据湖就是普通 Parquet 文件，Polars / pandas / Spark 均可直接扫描：

```python
import polars as pl
bars = pl.scan_parquet("data/stock-data-engine/curated/daily_bars/**/*.parquet")
df = bars.filter(pl.col("symbol") == "600519.SH").collect()
```

### 数据湖目录

```
{data_root}/
  curated/   {dataset}/{partition}=v/part-*.parquet   # 每主键一行 canonical（下游只读这里）
  derived/   adj_factors/...                          # 派生数据
  staging/   本次 run 原始落地（compact 后可清理）
  meta/      manifest.db、质量 findings、增量水位、on-demand 缓存
  duckdb/    stockdata.duckdb（视图层）
```

---

## 路线图（可实施步骤）

> 原则：**先纵深后广度**——先让 L0/L1 可信、可回填、日更稳定（所有因子的地基），再逐层铺开。

### Phase 0 数据可信基线 ✅ 已完成

mock 静默兜底改为显式失败（新增 `allow_mock` 门控 + 审计拦截）、`fetched_at` 统一 UTC timestamp、manifest 开 WAL、包结构按数据层重组。

### Phase 1 P0 真实化 + 稳定日更（对应里程碑 M2，🚧 当前阶段）

| # | 任务 | 关键文件 | 验收 |
|---|------|----------|------|
| 1 | trading_calendar 真实化（交易所日历种子 CSV + 指数 bars 推导兜底） | `adapters/`, `steps/reference.py` | 节假日不出现在交易日中 |
| 2 | corporate_actions 真实化（mootdx `xdxr`，备源 eastmoney） | `adapters/tdx_protocol/` | 除权日触发 `symbols_to_rebackfill` |
| 3 | daily_bars 分页回填（突破 mootdx 800 条限制） | `orchestrator/worker_pool.py` | 能回填至 2016 年 |
| 4 | 增量水位 `meta/state/`（按数据集记录 last-success 日期） | `storage/`, `steps/` | 日更只抓水位之后 |
| 5 | 批级 manifest + 续跑（symbol-batch 粒度入库） | `orchestrator/manifest.py`, `worker_pool.py` | `sde retry` 只重跑失败 batch |
| 6 | adj_factors 并行 + 缓存（仅除权日/新股重抓） | `derive/adj_factors.py` | 全市场 < 10 分钟 |
| 7 | trading_status 真实化（eastmoney ST/停牌） | `adapters/eastmoney/` | ST 股可被 universe 过滤 |

**出口标准**：P0 七数据集全部真实源、连续两周日更成功率 ≥99%、同窗口重复 run 结果幂等、PK 唯一率 100%。

### Phase 2 消费层（Python API + PIT 契约）

1. `query/reader.py`：`load(dataset, start, end, adjust, universe, as_of)`（DuckDB/Polars 实现）。
2. 复权组合、universe 过滤（instruments + trading_status）内置。
3. **提前锁定 PIT 契约**：财报类 schema 必须含 `announce_date`，写入 PRD 附录 A。

### Phase 3 数据面铺开（M3 → v1.1）

按 PRD §4.2/§4.4 顺序，每个数据集走 CONTRIBUTING 的 definition-of-done（schema+PK+step+测试+文档）：
资金面/估值/公告（fund_flow, valuation_metrics, northbound_*, margin_trading, sector_members, announcement_index, dragon_tiger, block_trades）→ 基本面/结构（financial_statement_items 含公告日, index_constituents, industry_members）→ 宏观/情绪/风控（macro_indicators, market_breadth, share_unlock_schedule, 新闻先 on-demand 后 batch）。

### Phase 4 多源健壮性（M4）

备源 snapshot、跨源抽样审计（价格 ±10bps）、failover 语义按 ADR-0003：永不静默切源。

---

## 文档

- [docs/PRD.md](docs/PRD.md) — **统一需求文档**（产品定义、架构、数据清单、里程碑；附录 A Schema 契约 / B 数据集目录 / C 运维手册）
- [docs/adr/](docs/adr/) — 架构决策记录
- [CONTRIBUTING.md](CONTRIBUTING.md) — 开发约定与新数据集 definition-of-done
- [CHANGELOG.md](CHANGELOG.md) — 变更记录

## License

MIT
