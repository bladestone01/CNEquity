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
| L0 基础参考 | Universe、交易日历、停复牌/ST | 可交易过滤、回测窗口 | instruments, trading_calendar, trading_status | ✅ M2 |
| L1 行情 | 未复权日线 + 复权因子 | 动量、波动、量价因子 | daily_bars, index_bars, adj_factors | ✅ M2 |
| L2 公司事件 | 除权除息、公告索引 | 事件驱动、除权回补 | corporate_actions, announcement_index | ✅ M2/M3 |
| L3 基本面 | 财报科目、估值、一致预期 | 价值/质量/成长因子 | financial_statement_items, valuation_metrics, analyst_consensus | ✅ v1.1 |
| L4 资金面 | 北向、融资、主力、龙虎榜 | 聪明钱、杠杆情绪 | fund_flow, northbound_*, margin_trading, dragon_tiger, institutional_holdings | ✅ v1.1 |
| L5 结构行业 | 板块、指数成分、行业分类 | 行业中性、板块轮动 | sector_members, index_constituents, industry_members | ✅ M3+ |
| L6 宏观 | 利率、货币、景气指标 | 宏观择时、风格切换 | macro_indicators | ✅ v1.1 |
| L7 舆情文本 | 新闻、研报、情绪得分 | NLP 特征、事件挖掘 | stock_news, research_reports, sentiment_scores | ✅ v1.1 |
| L8 风险合规 | 解禁、监管处罚 | 负面清单、风控过滤 | share_unlock_schedule, regulatory_events | ✅ v1.1 |

完整字段契约、主键、分区键与逐源限制见 [docs/PRD.md](docs/PRD.md)（附录 A/B）。

> ⚠️ 上表 ✅ 指 **step/schema/adapter 已实现**。分组运行（`--group`）各组末尾含
> `compact`，抓取完成后写入 curated（R-15/R-23 已修复）。其余已知缺陷见
> [docs/PRD.md §10 风险登记册 / §11.1 v1.2 修复计划](docs/PRD.md)。

## 数据可信原则

1. **永不伪造**：数据源失败即判 batch failed，绝不静默返回假数据（mock 仅限测试开关 `allow_mock`，且强制标记 `source="mock"`，审计自动拦截）。✅
2. **可溯源**：每行 curated 数据带 `source / data_version / fetched_at` 三列。✅
3. **口径可重算**：日线存**未复权**价 + 独立复权因子，qfq/hfq 在查询期组合，不污染原始数据。✅
4. **多源不打架**：curated 每主键一行 canonical；备源进 snapshot，审计出 diff，永不自动切源。✅ M4
5. **无前视偏差**：财报等低频数据带公告日 `announce_date` 双时间轴，按「截至当日已公告」对齐（`load(..., as_of=)`）。✅ M3+

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

**首次全量 init** 完成后，按 [docs/PRD.md 附录 C — Post-backfill acceptance](docs/PRD.md#post-backfill-acceptance回填完成验收) 做幂等/口径/覆盖/消费层验收；可执行：

```bash
.venv/bin/python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# 同窗口重跑 daily 后再：
.venv/bin/python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

## 使用数据（三种方式，任选）

### 1. Python API（推荐，✅ Phase 2）

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

### Phase 1 P0 真实化 + 稳定日更（对应里程碑 M2，✅ 已完成）

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

### Phase 2 消费层（Python API + PIT 契约）✅ 已完成

1. `query/reader.py`：`load(dataset, start, end, adjust, universe, as_of)`（Polars 实现，DuckDB 视图仍可用于 SQL）。
2. 复权组合、universe 过滤（instruments + trading_status）内置。
3. **PIT 契约**：`financial_statement_items` schema 含 `announce_date`，PRD 附录 A 已锁定；`load(..., as_of=)` 按公告日过滤。

### Phase 3 数据面铺开（M3 → v1.1，✅ M3 + M3+ 已完成）

**M3 batch**：fund_flow、northbound_*、margin_trading、valuation_metrics、sector_members、announcement_index、dragon_tiger、block_trades

**M3+ batch**：financial_statement_items（PIT/`announce_date`）、index_constituents、industry_members

**v1.1 宏观/风控 batch**：macro_indicators、market_breadth（自算）、share_unlock_schedule、regulatory_events

**v1.1 P2 research batch**：institutional_holdings、analyst_consensus、sentiment_scores（公告关键词 + 个股新闻 NLP）

```bash
pip install -e ".[macro]"   # 可选：akshare 补充 PMI/M2/社融 等月度宏观指标
pip install -e ".[nlp]"     # 可选：SnowNLP 增强 stock_news / sentiment 打分

sde query --dataset stock_news --symbol 600519.SH --config configs/stockdata.toml
sde run daily --group fundamentals --config configs/stockdata.toml   # 17:30
sde run daily --group macro_risk --config configs/stockdata.toml     # 18:00（同上）
sde run daily --group research --config configs/stockdata.toml       # 18:30（同上）
```

**待办**：先完成 Phase 5 正确性修复，再做连续两周生产日更稳定性观察

### Phase 4 多源健壮性（M4）✅ 已实现

- 备源 snapshot → `meta/source_snapshots/`（主源 batch 失败时 EastMoney 日线；`corporate_actions` daily 对除权 symbol 快照 TDX，backfill 快照 EM）
- `audit` 产出 `meta/quality/source_diffs/{run_id}.json`（价格 ±10bps 抽样比对）
- ADR-0003：**永不自动切源**，canonical 仅写主源

### Phase 5 正确性修复批次（v1.2）🚧 当前最高优先级

2026-07-06 全库架构评审结论：架构方向（四层湖 / schema 契约 / 自研编排）不变，但存在会
污染下游选股结论的正确性缺陷，须先于任何新数据集修复。完整清单与验收标准见
[docs/PRD.md §11.1](docs/PRD.md)，摘要：

| 优先级 | 内容 | 风险号 |
|--------|------|--------|
| P0 | 分组 run 自动追加 compact→audit（修「数据滞留 staging」与「audit 先于 compact 执行」） | R-15/R-23 ✅ |
| P0 | corporate_actions daily 主源修复（daily=EM canonical，backfill=TDX xdxr） | R-17 ✅ |
| P0 | 部分批失败时不推水位 + retry 后自动 compact（消除永久数据空洞） | R-18 ✅ |
| P0 | instruments 合并式 compact，保留退市股（消除幸存者偏差）+ 补 list/delist_date | R-16 ✅ |
| P0 | TDX 日线分页早停（当前每日增量翻全历史，请求放大 ~8 倍） | R-19 ✅ |
| P1 | 分页失败 fail-loud + EM backfill 全分页；EM 跨进程限速；curated 原子写 | R-22/R-21/R-24 🟡 |
| P1 | 消费层 lazy scan + 分区裁剪；adj_factors 改 append-only | R-25/R-20（部分：水位目录扫描、无缓存 fail-loud） |
| P1 | CLI 默认 config、`job.init.phases.names`、`sde compact`/`backfill` | R-26 ✅ |

---

## 文档

- [docs/PRD.md](docs/PRD.md) — **统一需求文档**（产品定义、架构、数据清单、里程碑；附录 A Schema 契约 / B 数据集目录 / C 运维手册）
- [docs/adr/](docs/adr/) — 架构决策记录
- [CONTRIBUTING.md](CONTRIBUTING.md) — 开发约定与新数据集 definition-of-done
- [CHANGELOG.md](CHANGELOG.md) — 变更记录

## License

MIT
