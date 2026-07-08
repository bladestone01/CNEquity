# StockDataEngine 架构设计（分层 + 差距分析）

版本：v1.0
日期：2026-07-07
定位：本文回答「系统怎么分层、边界在哪、离支撑实盘赚钱还差什么」。
数据集需求与风险登记见 [PRD](PRD.md)；排期与优先级见 [roadmap](roadmap.md)；关键决策见 [ADR](adr/)。

---

## 1. 评价镜头：这个系统怎么算「能赚钱」

引擎本身不产生 alpha——alpha 在下游 StockWorkbench 的因子与策略里。引擎决定的是下游三件事的**可信度**，这也是本文所有设计取舍的评价标准：

| 维度 | 含义 | 失败形态 |
|------|------|----------|
| **回测结论可信** | 数据正确 + PIT 无前视 + universe 无幸存者偏差 | 假收益/断裂因子 → 回测曲线好看但实盘亏钱 |
| **信号及时** | 每交易日收盘后数据按时新鲜，下游 `wb daily` 可依赖 | 漏更一天 = 信号断档，实盘节奏被打断 |
| **结果可追溯** | 口径可重算、来源可审计，复盘时能解释每一笔 | 数字对不上却查不出为什么，信任崩塌 |

Workbench M2 的实证已经给出教训：vol_20d 因子 ICIR 0.54 五层完美单调，但 naive TopN 扣成本后 Sharpe 仅 0.28——**强因子 ≠ 赚钱策略**。策略侧的迭代空间靠 Workbench；引擎侧能做且必须做的，是保证「迭代所依据的数据不说谎」。

---

## 2. 设计分层（现状 → 目标态）

现有代码已覆盖 1–5 层；**第 6 层（运行保障）是当前缺失的一层**，也是「能日常实盘使用」与「只是个数据工具」的分界。

```
┌─────────────────────────────────────────────────────────────┐
│ 下游消费方：StockWorkbench（因子/策略/回测/wb daily）           │
└──────────────────────────┬──────────────────────────────────┘
                           │ load() / DuckDB / Parquet 直读
┌──────────────────────────▼──────────────────────────────────┐
│ 5. 消费契约层  query/          load() API、DuckDB 视图、PIT   │
├─────────────────────────────────────────────────────────────┤
│ 4. 质量保障层  quality/        run 级 findings、湖级 health、  │
│                                跨源 diff；【缺】跨数据集对账    │
├─────────────────────────────────────────────────────────────┤
│ 3. 湖存储层    storage/ derive/  staging→curated→derived→meta │
├─────────────────────────────────────────────────────────────┤
│ 2. 采集编排层  orchestrator/ steps/  Wave DAG、manifest、      │
│                                水位、compact 门禁、worker pool │
├─────────────────────────────────────────────────────────────┤
│ 1. 数据源适配层 adapters/      tdx(mootdx)/sina/eastmoney/     │
│                                cninfo/akshare，薄 I/O + 限速   │
├─────────────────────────────────────────────────────────────┤
│ 6. 运行保障层【目标态，当前缺失】                              │
│    调度（launchd/cron）、freshness SLO、告警、备份恢复         │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 各层职责与关键实现

| 层 | 目录 | 职责 | 关键实现 | 状态 |
|----|------|------|----------|------|
| 1 数据源适配 | `src/stock_data_engine/adapters/` | 各源协议封装，只做 I/O 与格式转换，不含业务逻辑 | `tdx_protocol/client.py`、`eastmoney/*`、`sina/adj_factors.py`、`cninfo/`、`macro/`；跨进程限速 `domain/rate_limit.py` | 🟢（EM 跨进程限速遗留 R-21） |
| 2 采集编排 | `orchestrator/` + `steps/` | Wave DAG 拓扑执行、批级 manifest、增量水位、失败不推水位、retry 只重跑失败 batch | `engine.py::JobEngine`、`registry.py`、`deps.py`、`manifest.py`（SQLite WAL）、`compact_gate.py`、`run_lock.py`、`storage/state.py` | 🟢 |
| 3 湖存储 | `storage/` + `derive/` | staging（run 原始落地）→ curated（每 PK 一行 canonical）→ derived（可重算派生）→ meta（水位/快照/质量） | `storage/parquet.py`（写前 schema 强校验 + 原子写）、`derive/adj_factors.py`；契约见 [ADR-0002](adr/0002-parquet-lake-over-database.md)/[ADR-0003](adr/0003-canonical-curated-with-source-snapshots.md) | 🟢 |
| 4 质量保障 | `quality/` | run 级 findings、湖级健康快照（`sde audit --full`）、跨源 diff、mock 拦截、行数突变 | `audit.py::lake_health()`、`dataset_checks.py`、`source_diff.py` | 🟡（见 §4 G5） |
| 5 消费契约 | `query/` | `load()` 读取 API（复权/universe/PIT 内置）、DuckDB 视图 | `reader.py::load()`、`views.py`；qfq 查询期派生见 [ADR-0004](adr/0004-store-hfq-derive-qfq-at-query.md) | 🟢（lazy scan 优化遗留 R-25） |
| 6 运行保障 | —— | 定时调度、数据新鲜度 SLO、失败告警、manifest/meta 备份 | **无实现**；PRD 附录 C 仅有 cron 示例文本 | 🔴 |

### 2.2 数据流（一次日更的完整路径）

```
16:00 收盘
  → [调度触发，当前手动]                                    …第 6 层缺口
  → Wave: reference(L0 并行) → corp_actions→daily_bars → index_bars
      每 step：adapter 拉取 → validate_dataframe 强校验 → staging
  → finalize: compact（manifest 门禁：本 run 有 failed batch 的
      数据集不合并、不推水位）→ derive_adj_factors → audit
  → curated/derived 就绪，meta/state 水位前移
  → 下游 Workbench：wb data status 门禁（16:00 截止语义）
      → 水位键控 panel 缓存自动失效 → 增量算因子 → wb daily
```

失败路径：batch failed → 水位不动 → `sde retry --run-id` 只重跑失败 batch → 全部成功后自动 compact→derive→audit。**当前失败只能靠人主动查 `sde status` 发现**（第 6 层缺口）。

---

## 3. 引擎 ↔ Workbench 契约（正式边界）

Workbench 的唯一数据入口是 `stock_data_engine.query.load()`，两侧已通过契约测试锁定。契约要点：

```python
load(dataset, *, start, end,
     adjust=None | "qfq" | "hfq",   # hfq 存储、qfq 查询期按窗口 anchor 派生（ADR-0004）
     universe=None | "all_a",       # 上市/退市 + ST/停牌过滤（后者仅 trading_status 覆盖日）
     as_of=...,                     # PIT：financial_statement_items 按 announce_date <= as_of
     symbols=..., items=...,
     strict_adj=False)              # True 时缺因子 fail-loud，不做 factor=1.0 降级
```

| 契约条款 | 内容 | 出处 |
|----------|------|------|
| hfq-only 研究路径 | Workbench 研究一律 `adjust="hfq"`；qfq anchor 随窗口漂移、不可复现，禁入研究 | Workbench D1 / ADR-0004 |
| fail-loud 复权 | Workbench 默认 `strict_adj=True`；引擎缺因子必须显式暴露（`adj_is_exact=False`），不得静默降级 | Workbench D2 |
| PIT 双时间轴 | 低频数据（财报/公告）必须带 `announce_date`，按「截至当日已公告」对齐 | PRD 附录 A |
| 水位即缓存键 | Workbench panel 缓存键含引擎水位（`meta/state/{dataset}.json`）；水位前移即自动失效 | Workbench D4 |
| 交易日主轴 | 窗口/warmup 一律按 trading_calendar 交易日计，禁自然日 | Workbench D3 |
| schema 只增不改 | curated 列只做 additive 演进，破坏性变更 bump `dataset_schema_version` | PRD 附录 A |

**契约的含义**：引擎侧任何影响上述条款的改动（列语义、复权口径、水位行为）都是 breaking change，必须先过 Workbench 契约测试再合并。

---

## 4. 差距分析：离「支撑实盘赚钱」还差什么

按 §1 三个维度对现状打分，缺口按赚钱影响排序（G1 最高）。湖内证据为 2026-07-07 实测。

### 4.1 回测结论可信

| # | 缺口 | 证据 | 影响 |
|---|------|------|------|
| **G1** | **adj_factors hfq 历史断裂** | Workbench 全市场回测发现 1479/6555（22.6%）股票因子历史断裂，单日假收益达千倍级（多为老股）。[ADR-0004](adr/0004-store-hfq-derive-qfq-at-query.md) 已定案 hfq-only 存储，但 append-only 增量 merge 与断裂根治未落地；Workbench 被迫自建 quality guard（\|adj_ret\|>0.35 剔除）自保 | 回测 universe 缩水 23%，或结论直接作废。**当前最高优先级** |
| **G2** | **snapshot 类数据集无历史** | 湖内实测：valuation_metrics / fund_flow / index_constituents / industry_members 各仅 1 个分区（2026-07-07）。`fetch_semantics="snapshot"` 的设计只抓「当日快照」，无历史回填路径 | Workbench Phase 1 三策略之一「价值（估值分位）」需要多年 PE/PB 历史算分位，**当前无法回测**；行业中性化同理缺历史归属 |
| **G3** | **trading_status 历史 ST 缺失** | 湖内仅 2026-07-06 起 2 个分区；EastMoney 不提供历史 ST/停牌 | 2016→2026-07 回测区间 `universe="all_a"` 不剔除历史 ST，存在前视/幸存者偏差（audit 已报覆盖起点警告，但只是「知道有偏」而非「消除偏差」） |
| G6a | northbound 口径收紧 | 2024-08 起北向逐日披露取消，只能按季末抓（湖内仅 2026-03-31 一个分区） | Phase 2 资金流策略可行性未定，需先做口径预验证 |
| G6b | index_bars 疑有 ~3% 交易日缺口 | Workbench 侧记录（task_265958c4）；引擎 4152a08（index() 修复）可能已根治，未验证 | 基准对齐误差 → 超额收益计算失真 |

### 4.2 信号及时

| # | 缺口 | 证据 | 影响 |
|---|------|------|------|
| **G4** | **无调度、无监控、无告警** | `scripts/` 下无任何 cron/launchd 配置；日更全靠手动执行；`sde audit --full` 有湖级健康检查能力但无人定时触发、结果无通知渠道 | `wb daily` 实盘闭环要求每交易日 16:00 后数据按时新鲜。手动模式下漏跑/失败无人知晓，一天断档就打断实盘节奏——**这是「工具」与「生产系统」的分界** |

### 4.3 结果可追溯

| # | 缺口 | 证据 | 影响 |
|---|------|------|------|
| **G5** | **audit 缺「收益率级别」护栏，防线错位** | G1 是 Workbench 算因子时才发现的——引擎 audit 有 PK/行数突变/跨源 diff，但没有「数据经济含义合理性」检查：无 adj_factors × corporate_actions 对账（因子跳变是否有对应除权事件解释）、无 bar-to-bar 收益极值扫描 | 数据投毒只能靠下游事后发现。正确的防线位置在引擎：**数据不合理就不该出湖** |
| G7 | 运维韧性弱 | manifest.db 无备份；`meta/source_snapshots/` 按 run_id 无限累积且 `read_latest` 全量 concat 越跑越慢；消费层 lazy scan 无分区裁剪下推（R-25） | 长期成本与恢复能力问题，不阻塞当前闭环 |

### 4.4 已解决、不再是缺口（避免按旧印象重复投入）

- trading_calendar / index_bars / daily_bars 均已回填至 2016（湖内实测 `trade_date=2016-01-04` 起连续分区）。
- financial_statement_items 已有 2016Q1–2026Q1 共 41 个报告期历史，且带 `announce_date` PIT 轴。
- R-15/R-23（分组运行不 compact、audit 先于 compact）已修复；分组模式可正常落 curated。
- CDR（689 段）已移出 `all_a`，不再污染 universe。

---

## 5. 设计原则（沉淀，供后续演进对照）

1. **正确性优先于覆盖面**：一个假数据集的伤害大于十个缺失数据集——假数据会直接转化为错误的交易决策。新数据集永远排在已有数据集的正确性修复之后。
2. **fail-loud 是底线**：任何「静默降级 / 静默截断 / 静默兜底」都是投毒路径（R-14/R-22 的教训）。失败必须显式：batch failed、水位不动、finding 落盘。
3. **防线放在引擎侧**：下游能发现的数据问题，引擎 audit 应该先发现（G5）。Workbench 的 quality guard 是自保手段，不是替代。
4. **口径可重算**：存未复权 + 独立因子、存 hfq 派生 qfq、PIT 双时间轴——一切口径都能从原始数据重放（ADR-0003/0004）。
5. **单人可运维**：自研编排而非 Airflow、Parquet 而非数据库、launchd/cron 而非平台——每个组件都要能被一个人理解、排障、重建。
