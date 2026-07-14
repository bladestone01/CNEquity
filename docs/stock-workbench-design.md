# StockWorkbench 详细设计（v0.1 草案）

> 独立于 stock-data-engine 的 A 股研究与选股分析系统。**已在 `/Users/chaosun/code/StockWorkbench`
> 落地实现。
>
> **当前架构文档**（攻/守双轨、L7 轮动）：Workbench 仓库 [`docs/architecture.md`](../../StockWorkbench/docs/architecture.md)。
> 本文档为 v0.1 草案，部分章节（因子数、里程碑）已过时，以 Workbench 代码与 `docs/overview.md` 为准。

---

## 0. 实施状态（2026-07-08）

MVP Phase 1 + 1.5 全部落地，111 测试全绿。核心闭环贯通：数据护栏 → 12 因子 →
三策略 + 融合 → fast/ledger 双模式对账回测 → 每日 `wb daily` 信号（append-only
ledger）→ 月度复盘。

| 里程碑 | 交付 | 状态 |
|--------|------|------|
| M0 骨架 | DataView（hfq-only/strict-adj/日历越界 fail-loud）、水位缓存、tradable_mask、`wb data status` 门禁、data/quality.py 坏复权护栏、引擎契约测试 | ✅ |
| M1 因子 | Factor 协议 + FactorStore 增量引擎（与全量重算逐字节一致）+ 6 量价因子 + IC/分层报告 | ✅ |
| M2 回测 | fast 引擎（T+1/涨跌停/停牌/退市/整手/成本、sell-then-scale-buy 防杠杆）+ 策略层 + 绩效 + BacktestReport + 涨跌停/T+1 golden tests | ✅ |
| M3 闭环 | `wb daily`（新鲜度门→算因子→目标持仓 diff→写信号→DailyBrief）+ SQLite ledger + RiskRules 黑名单 + regime | ✅ |
| M4 强化 | RegimeOverlay 择时 + walk-forward + `wb review --month` + ledger 精确引擎 + **fast/ledger 双模式对账（D5 自检，实测差 0.15%）** | ✅ |
| Phase 1.5 | PIT 基本面因子（roe/net_profit_yoy/revenue_yoy）+ 价值因子（ep_ttm/bp/sp_ttm）+ 3 个 Notebook | ✅ |

**因子库 12 个**：6 量价（mom_20d/mom_60d/rev_5d/vol_20d/amount_20d/amihud_20d）
+ 3 基本面 PIT（roe/net_profit_yoy/revenue_yoy）+ 3 价值（ep_ttm/bp/sp_ttm）。

**实证结论（全市场，仅供参考）**：vol_20d（ICIR 0.54）与 bp（ICIR 0.40、单调 +1.0）
最强；A 股 60 日动量呈反转，EP 弱于 BP（亏损股 1/PE 伪影）。三因子融合
`vol_20d+bp+roe`（低波+价值+质量）CAGR 12.95%/Sharpe 0.84/最大回撤 -16.8%/超额 +13.2%
——多因子分散把回撤砍到单因子的一半，是系统核心成果。

**通过本项目反向发现并上报的引擎数据问题**：① adj_factors hfq 历史断裂（22.6% 股票，已修）
② trading_calendar 调休 75 天误标 ③ index_bars 覆盖 ④ corporate_actions 分红每股/送转每10股
量纲不一致（ledger 已本地兼容 /10，对账从差 48% 收敛到 0.15%）。

**待引擎数据的剩余缺口**：size 因子/市值中性化（valuation total_mv 为 null）、
行业中性化（industry_members）、资金面（fund_flow 需先验证北向口径）、大师预设插件（远期）。

---

## 1. 定位与需求

### 1.1 一句话

StockWorkbench 把「想法 → 因子 → 回测 → 组合 → 每日信号 → 复盘」变成一条可重复、
防偏差的个人研究流水线，数据只来自 stock-data-engine 的 `load()` 契约。

### 1.2 设计哲学（以赚钱为目的的推论）

个人低频量化赚钱的来源不是某一次预测，而是**过程质量**：

1. **防偏差 > 花哨模型**。前视偏差、幸存者偏差、成本低估三项中任何一项失守，
   回测收益都是幻觉。系统的第一职责是让这三类错误在架构上难以发生。
2. **决策闭环 > 研究广度**。v0.1 就必须能在每个交易日收盘后回答「明天怎么办」，
   而不是先攒一堆因子库。没有每日跟踪的回测系统不产生收益。
3. **频率定位：日频 EOD 决策**。引擎只有日线；个人无日内基础设施。持仓周期
   5–60 交易日，周/月调仓，T+1 现实约束。不做日内、不做高频、不做期货期权。
4. **纪律外置**。walk-forward 切分、holdout 只跑一次、paper 跟踪 ≥3 个月再上真金，
   这些纪律写进工具流程与报告水印，而不是靠自觉。

### 1.3 功能需求

| ID | 需求 | 说明 |
|----|------|------|
| FR1 | 数据访问 | DataView：交易日对齐、宽表面板（date × symbol）、可交易性掩码、缓存 |
| FR2 | 因子 | 定义/注册/版本化/增量计算/存储/评估（IC、分层、衰减、换手） |
| FR3 | 筛选 | 声明式选股表达式 + 硬性风控黑名单（两级分离） |
| FR4 | 策略 | 因子组合 → 打分 → 目标持仓权重；regime 择时 overlay 可开关 |
| FR5 | 回测 | A 股微观规则正确的日频回测（T+1、涨跌停、停牌、整手、印花税、退市） |
| FR6 | 报告 | FactorReport / BacktestReport / DailyBrief，自包含 HTML + Markdown 双格式 |
| FR7 | 跟踪 | paper ledger、每日信号、实盘成交记录、实盘 vs paper 偏差、月度复盘 |
| FR8 | 双入口 | CLI（typer）+ Notebook API（`import stock_workbench as wb`） |

### 1.4 非功能需求

- **正确性 > 性能**：PIT 纪律、可交易性约束优先于一切优化。
- **可复现**：每次回测产出 run manifest（策略代码 hash、配置、因子版本、数据水位）。
- **规模**：~5,400 股 × ~2,500 交易日（2016 至今）≈ 1,300 万行日线（parquet 237MB）；
  单字段面板 2500×5400 float64 ≈ 108MB —— 全内存 polars 无压力，不需要分布式。
- **时延目标**：10 年全市场单策略回测 < 30s；因子全量计算 < 5min，水位增量 < 30s。
- **形态**：单机、无常驻服务、无重数据库（仅 SQLite 做账本）；Python 3.12+，polars 栈。
- **可产品化**：领域层接口 Protocol 化、纯函数化，未来 Web/API 包装不改内核。

### 1.5 硬约束

- **唯一数据源 = `stock_data_engine.load()`**。Workbench 内禁止出现任何外部 HTTP
  抓取；文本/新闻类按需数据走引擎的 `OnDemandService`。
- **当前数据边界**：引擎已落地 L0/L1/L2 + market_breadth + regulatory_events
  （9 个 curated + adj_factors）。L3 基本面、L4 资金面、L5 行业尚未回填 ——
  v0.1 因子集只能是量价 + 事件类，基本面因子在架构上预留、实现上 blocked。
- 引擎 PRD §4.5 已声明「不在 StockDataEngine 内实现因子逻辑」——因子、策略、
  回测全部归属 Workbench，引擎只增数据集不增语义。

---

## 2. 总体架构

### 2.1 分层

```
┌────────────────────────────────────────────────────────────┐
│ 接口层    CLI (typer)              Notebook API (wb.*)      │
├────────────────────────────────────────────────────────────┤
│ 应用层    research 流程        daily 流程        report      │
│           (因子评估/回测编排)   (信号生成/diff)   (渲染)      │
├────────────────────────────────────────────────────────────┤
│ 领域层    Factor   Screen   Strategy   Backtester           │
│           CostModel   RiskRules   Metrics   (全部纯函数)     │
├────────────────────────────────────────────────────────────┤
│ 数据层    DataView(对齐/面板/掩码/缓存)   FactorStore        │
│           RunStore   Ledger(SQLite)                         │
├────────────────────────────────────────────────────────────┤
│ stock-data-engine    load() / OnDemandService / meta 水位   │
└────────────────────────────────────────────────────────────┘
```

依赖方向严格自上而下；领域层不知道文件系统（数据层注入 DataFrame），
这是产品化的关键：未来 FastAPI 包一层应用层即可，多用户隔离只动数据层。

### 2.2 仓库结构

```
stock-workbench/                     # 独立 git 仓库
├── pyproject.toml                   # stock-data-engine 以 path/git dep 引入
├── configs/workbench.toml
├── src/stock_workbench/
│   ├── data/          # DataView, panel, tradable_mask, cache, freshness
│   ├── factors/       # protocol, registry, builtin/, engine.py, store.py
│   ├── screen/        # 表达式筛选 + blacklist 风控
│   ├── strategy/      # protocol, combinators(TopN/打分/中性化), regime.py
│   ├── backtest/      # engine.py, costs.py, tradability.py, manifest.py
│   ├── eval/          # factor_eval.py(IC/分层/衰减), performance.py(绩效)
│   ├── report/        # jinja2 模板 + 渲染，数据(json)与模板分离
│   ├── track/         # ledger.py, daily.py, review.py
│   ├── cli/           # typer app
│   └── nb.py          # notebook 顶层便捷 API
├── strategies/        # 用户策略与自定义因子（py 文件，git 管理）
├── notebooks/         # 研究草稿（不进 CI）
└── tests/             # unit + golden + engine 契约测试
```

### 2.3 workspace 数据目录（与代码库分离，配置指定）

```
~/stock-workbench-data/
├── factors/                    # FactorStore：每因子每版本一目录
│   └── mom_60d@2/year=2024/part.parquet
├── runs/                       # RunStore：每次回测一目录
│   └── 20260706T1530_mom_topn/
│       ├── manifest.json       # 代码hash/配置/因子版本/数据水位/holdout计数
│       ├── nav.parquet         # 净值序列
│       ├── positions.parquet   # 逐日持仓
│       └── trades.parquet      # 成交与成本分解
├── reports/                    # 自包含 HTML
├── ledger.db                   # SQLite：signals / orders / positions / fills
└── cache/                      # DataView 面板缓存（可随时整目录删除）
```

文件布局即 API：parquet + manifest.json 的目录约定是稳定契约，
未来 Web 端直接读 RunStore/FactorStore，不需要 Workbench 进程在场。

---

## 3. 数据层：DataView

### 3.1 职责

把引擎 `load()` 的长表变成研究友好形态：交易日对齐、宽表面板、可交易性掩码、
本地缓存。**Workbench 全部数据入口收敛到这一个类**，方便统一实施 PIT 纪律。

### 3.2 API

```python
class DataView:
    def __init__(self, start: date, end: date, *,
                 universe: str = "all_a", data_root: Path | None = None): ...

    def calendar(self) -> list[date]                      # trading_calendar 主轴
    def bars(self, adjust: str = "hfq") -> pl.DataFrame   # 长表，strict_adj=True
    def panel(self, field: str = "adj_close",
              adjust: str = "hfq") -> pl.DataFrame        # 宽表 date × symbol
    def index_bars(self, symbol: str = "000300.SH") -> pl.DataFrame
    def tradable_mask(self) -> pl.DataFrame               # date×symbol bool
    def breadth(self) -> pl.DataFrame                     # market_breadth
    def fundamentals(self, items: list[str]) -> pl.DataFrame
        # PIT 逐日展开面板（等 L3 落地；as_of=每个 trade_date）
```

### 3.3 关键设计决策

**D1：研究与回测只用 hfq（后复权），qfq 禁入研究路径。**
引擎 reader 的 qfq 是查询期派生：anchor = 窗口内最新 bar 日期（ADR-0004），
同一因子在不同回测窗口下 qfq 价不同 → 因子值不可复现。hfq 因子跨窗口稳定，
且 hfq 收益率天然含分红再投资。qfq 只允许出现在给人看的报表展示层。

**D2：`strict_adj=True` 默认开启。**
宁可 fail-loud 也不接受 `factor=1.0` 静默降级污染收益序列；
`adj_is_exact=False` 的行进入回测属于数据事故，必须在 `wb data status` 阶段暴露。

**D3：一切时序以 trading_calendar 为主轴。** 禁止自然日运算；
窗口类因子（如 mom_60d）的 60 一律指交易日。

**D4：面板缓存以数据水位为失效键。**
缓存键 = (dataset, field, adjust, universe, start, end, watermark)；
watermark 取该数据集 curated 分区的 max(trade_date)（必要时读引擎 meta 的
ingestion_runs）。引擎日更后水位前移，旧缓存自动失效，无需手动清理。

### 3.4 tradable_mask（可交易性掩码）

逐日逐股布尔矩阵，回测与筛选共用，规则合取：

| 规则 | 数据来源 | 备注 |
|------|----------|------|
| 已上市且未退市 | instruments（list_date/delist_date） | 引擎保留退市股 → 天然防幸存者偏差 |
| 非停牌、非 ST/*ST | trading_status | **数据边界**：无历史 ST 回填，覆盖起点之前不过滤，报告必须标注 |
| 上市满 N 交易日 | instruments + calendar | 默认 N=60，避开新股 |
| 非退市整理期 | instruments + regulatory_events | |

涨跌停不进 mask（它不是"不可持有"而是"当日单边不可成交"），由回测执行层处理。

---

## 4. 因子层

### 4.1 Factor 协议（产品化接口 #1）

```python
class Factor(Protocol):
    name: str                # 唯一标识，如 "mom_60d"
    version: str             # 逻辑变更必须 bump，参与存储路径与缓存键
    requires: set[str]       # 依赖的引擎数据集，用于可用性预检
    warmup: int              # 所需历史窗口（交易日），计算引擎自动向前多取

    def compute(self, view: DataView) -> pl.DataFrame:
        """返回长表 [trade_date, symbol, value]。
        PIT 契约：value 只能使用 <= trade_date 的信息。"""
```

- 注册：`@register_factor` 装饰器；内置因子在包内，用户因子放 `strategies/`
  目录并被自动发现。`requires` 与引擎 `CURATED_DATASETS` 做启动期预检 ——
  依赖数据集未落地的因子标记 `blocked`，CLI 里可见但不可算（对齐引擎交付节奏）。
- **PIT 实施**：`compute` 收到的 DataView 已按 [start − warmup, end] 切窗；
  基本面数据只能通过 `view.fundamentals()`（内部强制 as_of=trade_date 逐日展开，
  展开结果作为 daily 面板缓存，摊销 PIT 查询成本）。框架不能证明任意用户代码
  无前视，但把「容易走的路」全部铺成 PIT 安全的。

### 4.2 FactorStore 与增量计算

- 路径 `factors/{name}@{version}/year=YYYY/part.parquet`，按年分区。
- 计算引擎对比因子已存 max(trade_date) 与数据水位，只补算缺口
  （附 warmup 回看），复用引擎的水位思想。
- 单因子独立目录而非全因子大宽表：增删因子互不影响、多版本共存、
  增量补算简单；跨因子 join 留到评估/策略消费时做（polars join 很便宜）。

### 4.3 v0.1 内置因子集（只依赖已落地数据）

| 类别 | 因子 | 数据 |
|------|------|------|
| 动量 | mom_20d / mom_60d / mom_120d（跳过最近 5 日） | daily_bars + adj_factors |
| 反转 | rev_5d | 同上 |
| 波动 | vol_20d、maxdd_60d | 同上 |
| 量价/流动性 | amount_20d、量比、换手变化率、Amihud 非流动性 | daily_bars |
| 事件 | 公告密度（announcement_index）、除权临近（corporate_actions） | L2 |
| 市场状态 | breadth 百分比、指数均线状态（供 regime，非截面因子） | market_breadth, index_bars |

扩展节奏（2026-07-06 拍板）：
- **估值因子（EP/BP 分位等）进 Phase 1**：引擎同步交付 `valuation_metrics`
  （日频快照、trade_date 键控、无 PIT 难题，是解锁成本最低的 L3 数据集）。
- **质量/成长因子进 Phase 1.5**：等 `financial_statement_items`（`announce_date`
  PIT 契约 + 历史公告日回填质量是真正难点，单独做仔细）。
- **资金面因子进 Phase 2，且先做数据可得性验证**：北向数据披露口径 2024-08 起
  收紧（实时/当日净买入不再披露），历史一致性与存续性需先验证再立项。

### 4.4 因子评估（eval.factor_eval）

逐 rebalance 日截面：rank IC 序列（均值/ICIR/t 值）、IC 衰减曲线（1/5/10/20 日）、
分 5 层组合收益与单调性、多空组合净值、换手率、截面覆盖率。输出 FactorReport。
评估一律在 tradable_mask 过滤后的截面上做 —— 含 ST/停牌股的 IC 是虚高的。

---

## 5. 筛选层：两级分离

**硬性黑名单（RiskRules，不可关闭）**：ST/*ST、停牌、上市 < 60 交易日、
退市整理期、监管处罚 lookback 1 年（regulatory_events）、（未来）解禁高峰
（share_unlock_schedule 落地后）。语义是「这钱不赚」。

**策略筛选（Screen，声明式）**：

```python
screen = Screen(
    F("mom_60d").rank(pct=True) >= 0.8,
    F("vol_20d").rank(pct=True) <= 0.5,
    F("amount_20d") >= 5e7,        # 流动性下限：小资金也要设，保证滑点模型可信
)
```

`F()` 是因子面板引用，Screen 编译为 polars 表达式在 rebalance 日截面上求值，
输出候选池。Screen 可独立回测（等权持有候选池）作为策略的 baseline。

---

## 6. 策略层

### 6.1 Strategy 协议（产品化接口 #2）

```python
class Strategy(Protocol):
    name: str
    version: str
    factors: list[str]              # 声明依赖，用于预检与 manifest
    schedule: Rebalance             # weekly / monthly / 自定义交易日谓词

    def target_weights(self, ctx: StrategyContext) -> pl.DataFrame:
        """ctx: 当日因子截面、当前持仓、regime 状态、tradable 集合。
        返回 [symbol, weight]，weight 之和 <= 1（余为现金）。"""
```

### 6.2 内置组合器

- **TopN 等权**：Screen 候选池按主因子取前 N（默认 N=20，个人资金分散度上限）。
- **多因子打分（固定权重融合）**：各因子截面 z-score（去极值 MAD → 标准化）
  按**固定配置权重**加权合成后排序；MVP 不做 IC 加权/优化学习，权重进 manifest。
- **行业中性**（blocked，等 industry_members）：行业内排名替代全市场排名。

Phase 1 策略集（2026-07-06 拍板）：**动量、价值（估值分位，依赖 valuation_metrics）、
低波+反转（纯量价）** 三策略 + 固定权重融合出综合打分；资金流策略移至 Phase 2
（数据可得性先验证）。每日输出 = 综合 Top 30 股票池 + 各策略子池，
全部经过 tradable_mask 与风控黑名单，且信号自第一天起写入 append-only ledger。

### 6.3 Regime overlay（择时，独立于选股）

基于 index_bars（000300 的 200 日均线）+ market_breadth（站上 20 日线比例）的
简单二态 regime：risk_off 时对目标权重乘仓位系数（默认 0.5，可配 0）。
作为 overlay 独立开关，回测报告强制并排展示 overlay on/off 两条净值 ——
防止把择时运气误认为选股能力。

---

## 7. 回测引擎（正确性核心，系统含金量所在）

### 7.1 形态

**自研「向量化信号 + 逐日执行循环」混合引擎**：因子/信号/目标权重全部向量化
预计算；执行层逐交易日循环，处理路径依赖约束（T+1 持仓、涨跌停滚单、停牌冻结、
现金约束、整手）。日频 + 2,500 日循环，纯 Python 循环体也在秒级。

### 7.2 A 股微观规则清单（每条都是钱）

| # | 规则 | 实现 |
|---|------|------|
| R1 | T+1 | 当日买入份额锁定，次日方可卖出 |
| R2 | 信号时点 | T 日收盘数据出信号，T+1 执行；执行价可配 `open`（默认）/ `ohlc4` / `close` |
| R3 | 涨跌停不可成交 | T+1 开盘触及涨停 → 买单作废；跌停 → 卖单顺延至下一可成交日。判定用**未复权价**对前收盘：主板 ±10%、创业板/科创板 ±20%、ST ±5%、新股上市首 5 日无限制（板别由 symbol 前缀 + instruments 推断） |
| R4 | 停牌 | 持仓停牌 → 冻结，按最后成交价估值；买单作废 |
| R5 | 成本 | 佣金万 2.5（最低 5 元）、印花税卖出 0.05%（**参数化**，税率历史上变过）、过户费 0.001%、滑点默认固定 bp（v0.2 按 amount 分层） |
| R6 | 整手 | 100 股/手向下取整（科创板 200 股起）；个人资金规模下不可忽略 |
| R7 | 退市 | universe 含退市股（引擎已保留）；退市按最后价清仓并计退市损失参数 |
| R8 | 账户 | 现金账户：无融资、无做空、无 T+0 |
| R9 | 分红 | 见 D5 双模式 |

**D5：fast / ledger 双模式，互为对账。**
- *fast 模式*（v0.1）：hfq adj_close 收益率向量化计算，分红再投资已隐含；
  快、适合研究迭代。
- *ledger 模式*（v0.2）：未复权价 × 真实股数记账，corporate_actions 驱动
  现金分红入账与送转股数调整；慢但精确，适合上实盘前的最终验证。
- 同一策略双模式净值差异应在成本项量级内 —— 超出即引擎 bug 或数据 bug，
  作为持续自检（golden test 常驻 CI）。

### 7.3 可复现：run manifest

每次回测写入 `runs/{ts}_{strategy}/manifest.json`：策略与因子代码 hash、
完整配置快照、各因子 name@version、各数据集水位（max date）、引擎版本、
**holdout 计数器**（该策略在 holdout 区间上第几次运行 —— 直接印在报告水印上，
对抗过拟合的纪律外置，见 R4）。

### 7.4 绩效指标（eval.performance）

年化收益/波动、Sharpe、Calmar、最大回撤及区间、胜率/盈亏比、年均换手、
成本拖累分解（佣金/印花税/滑点分项）、对 000300 / 000905 / 中证全指的超额及
跟踪误差、分年度表、月度收益热力图。基准指数直接来自 index_bars。

---

## 8. 报告层

- jinja2 模板 + 数据(json) 分离；渲染为**自包含 HTML**（图表内嵌 SVG/plotly，
  离线可看、可存档、可邮件），同时产出 Markdown 版（终端可读，也便于喂给
  LLM 做复盘对话）。
- 三类报告：
  1. **FactorReport**：IC/ICIR、衰减、分层、换手、覆盖率、数据边界标注。
  2. **BacktestReport**：净值/回撤、指标表、成本分解、持仓快照、
     regime on/off 对比、**数据边界与 holdout 计数水印**。
  3. **DailyBrief**：今日信号、目标持仓 vs 当前持仓 diff（买卖清单+理由）、
     风控警报（持仓股进入黑名单）、市场 regime 状态、数据新鲜度检查结果。

---

## 9. 跟踪层（赚钱闭环）

### 9.1 每日流程 `wb daily`

```
引擎日更完成 → wb daily：
  1. 数据新鲜度检查（各数据集水位 == 最新交易日，否则 fail-loud 拒绝出信号）
  2. 增量补算激活策略依赖的因子
  3. 逐策略生成目标持仓 → 与 ledger 当前持仓 diff
  4. 写 signals 表（append-only，永不删改）→ 渲染 DailyBrief
```

### 9.2 Ledger（SQLite）

- `signals`：每日每策略信号全量历史 —— 之后做信号命中率与「如果完全跟单」的
  影子净值；**append-only 是复盘可信度的根基**。
- `orders` / `fills` / `positions`：paper 自动记账；实盘时用
  `wb ledger record-fill` 把真实成交录入，系统持续计算
  **实盘 vs paper 偏差**（执行滑点、延迟成本）—— 这是滑点参数的现实校准来源。

### 9.3 复盘 `wb review --month`

月度：策略 vs 基准、信号命中率、执行偏差分解、在用因子的近期 IC 对比其历史
分布（衰减警报）。纪律：paper ≥ 3 个月且实盘偏差可解释，才允许加真金。

---

## 10. 接口层

### 10.1 CLI（typer）

```
wb data status                          # 引擎水位/新鲜度/adj_is_exact 检查
wb factor compute [NAME|--all] [--since]
wb factor report NAME --start --end
wb screen run NAME --date
wb backtest run STRATEGY --start --end [--mode fast|ledger] [--overlay on|off]
wb backtest compare RUN_A RUN_B
wb daily [--date]                       # 核心日常命令
wb review --month YYYY-MM
wb ledger record-fill --symbol --qty --price --date
```

### 10.2 Notebook API（nb.py）

```python
import stock_workbench as wb

view = wb.view("2018-01-01", "2026-07-03")
f = wb.factor("mom_60d")
wb.eval.factor_report(f, view).show()          # inline HTML

run = wb.backtest(wb.strategy("mom_topn"), view, cost="personal")
run.report().show()
run.nav.to_pandas()                             # 生态兼容出口
```

polars 为内部标准，`.to_pandas()` 作为与 pandas 生态（quantstats 等）的边界适配。

---

## 11. 与 stock-data-engine 的契约管理

- 依赖方式：pyproject path/git dependency；**契约面 = `load()` 签名 + 数据集
  schema + 目录布局**，不 import 引擎内部模块（adapters/orchestrator 等）。
- Workbench 侧维护 **contract tests**：对每个消费的数据集断言列名/类型/
  PIT 语义（如 `financial_statement_items` 必须有 `announce_date`）；
  引擎升级破坏契约时在 Workbench CI 拦截，而不是在回测结果里发现。
- 数据新鲜度：`wb data status` 汇总各数据集水位 vs trading_calendar 预期，
  是 `wb daily` 的前置门。

---

## 12. 关键取舍（Trade-offs）

| # | 决策 | 备选 | 理由 |
|---|------|------|------|
| T1 | **自研回测引擎** | qlib / vectorbt / rqalpha / backtrader | T+1、涨跌停、ST、整手、印花税在通用框架里要么缺失要么需深度魔改；qlib 重且绑定自有数据格式；vectorbt 免费版难表达 T+1 滚单；rqalpha 事件驱动慢且维护弱。日频混合引擎约千行代码，polars 栈一致。风险=自己写出 bug → 双模式对账 + golden case 缓解；指标层可导出净值给 quantstats 交叉验证 |
| T2 | **向量化+逐日循环混合** | 纯事件驱动 | 日频/低换手下事件驱动无增益、慢一个量级；日内需求出现前不做（大概率永不出现） |
| T3 | **hfq-only 研究路径** | qfq | qfq anchor 随窗口漂移，因子不可复现（见 D1） |
| T4 | **polars** | pandas | 与引擎一致、面板性能好；`.to_pandas()` 兜底生态 |
| T5 | **单因子分区 parquet** | 全因子大宽表 | 增删/多版本/增量独立；join 成本可忽略 |
| T6 | **SQLite 账本 + parquet 分析** | 全 parquet | 账本要事务与 append-only 点查；分析数据要列式扫描 —— 各用所长 |
| T7 | **独立仓库** | 引擎 monorepo | 契约边界清晰（引擎 PRD 明言不含因子逻辑）；用 contract test 而非同仓耦合来管理演进 |
| T8 | **纪律外置到工具** | 靠自觉 | holdout 计数印在报告上、signals append-only、新鲜度门禁 —— 过拟合与数据事故是个人量化最大的两个亏钱来源 |

## 13. 风险与开放问题

| # | 风险 | 应对 |
|---|------|------|
| R1 | 引擎 L3/L4 未落地，基本面/资金面因子 blocked | 已拍板（2026-07-06）：引擎同步交付顺序 **valuation_metrics（Phase 1）> financial_statement_items（Phase 1.5）> industry_members > fund_flow（Phase 2，先验证北向披露口径变化后的可得性）** |
| R2 | trading_status 无历史 ST 回填 → 早期窗口 ST 过滤缺失 | 不造数据；回测报告强制标注数据边界起点 |
| R3 | 涨跌停判定的板别/新股/ST 细节 | 板别由 symbol 前缀推断 + instruments；规则表参数化并配 golden case（触板日实例）|
| R4 | 过拟合 | walk-forward 工具化：train 区探索、holdout 只跑一次；holdout 计数进 manifest 与报告水印 |
| R5 | 单人项目 scope 蔓延 | 里程碑收敛（§14），M3 之后必须「每天在用」再谈扩展 |
| Q1 | 滑点模型初值 | 先固定 bp（买卖各 10bp），实盘偏差数据积累后按 amount 分层校准 |
| Q2 | 指数基准的全收益处理 | index_bars 为价格指数；超额计算注明口径，后续引擎若增全收益指数则切换 |

## 14. 里程碑

| 阶段 | 周期 | 交付 | 验收 |
|------|------|------|------|
| M0 骨架 | 1–2 周 | 仓库/配置/DataView(panel+mask+缓存)/`wb data status`/契约测试 | status 全绿；面板缓存命中 |
| M1 因子 | 2 周 | Factor 协议+Store+增量引擎+6 个量价因子+FactorReport | mom_60d IC 报告可复现 |
| M2 回测 | 2–3 周 | fast 引擎+成本模型+绩效+BacktestReport+golden tests | TopN 动量策略 10 年回测 < 30s，涨跌停/T+1 case 通过 |
| M3 闭环 | 1 周 | `wb daily`+ledger+DailyBrief | **从此每个交易日实际使用** |
| M4 强化 | 2 周 | ledger 精确模式对账、regime overlay、walk-forward、`wb review` | 双模式净值差 < 成本量级 |
| M5 扩展 | 随引擎 | 基本面/资金面因子、行业中性化 | 跟随 L3/L4/L5 落地 |

**成功指标（赚钱导向）**：M3 后 DailyBrief 交易日可用率 100%；paper 跟踪
≥ 3 个月、实盘 vs paper 月偏差可解释且收敛，才投入真实资金；
任何策略上实盘前，holdout 运行次数 = 1。

### 14.1 MVP Phase 1 范围（2026-07-06 拍板）

映射到里程碑 M0–M3，验收即 M3 的「每天在用」：

| 项 | 内容 | 依赖 |
|----|------|------|
| 数据接入 | DataView 统一读行情（已落地）+ 估值（**引擎同步交付 valuation_metrics**）；财务 Phase 1.5、资金 Phase 2 | 引擎 |
| 因子 | ≤ 10 个：约 6 量价 + 2 估值 + 2 事件/市场状态 | M1 |
| 策略 | 动量 / 价值（估值分位）/ 低波+反转，三策略 | M1–M2 |
| 融合 | 固定权重 z-score 融合，权重入 manifest | M2 |
| 每日输出 | 综合 Top 30 股票池 + 各策略子池 + DailyBrief；信号 append-only 入 ledger | M3 |
| Notebook | 技术面、综合回测（Phase 1）；基本面（Phase 1.5，随财务数据） | M1–M2 |

**验收硬门槛**：Top 30 池上线前，回测引擎的涨跌停/T+1 golden test 必须通过；
信号从第一天起写入 ledger —— 没有这条脊柱，股票池只是一个无法事后验证的列表。

### 14.2 大师预设（远期，独立插件）

2026-07-06 拍板：**Phase 1/2 不做**，项目成熟后作为独立插件立项。届时边界：

- 形态 = Screen/Strategy 协议上的**命名预设**（格雷厄姆/林奇/巴菲特等价值-质量
  规则集，每个约数十行），不是新子系统；依赖 L3 财务数据。
- 必须过与自研策略相同的纪律：回测报告、holdout 水印、A 股适用性用数据说话。
- 不做不可量化项：主观宏观（索罗斯）、跨资产配置（达里奥，无债券/商品数据）；
  社媒个人策略仅当能写成明确规则清单，且不得引入引擎之外的数据抓取。
