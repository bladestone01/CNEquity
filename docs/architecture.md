# StockDataEngine 架构设计（分层 + 差距分析）

定位：系统怎么分层、边界在哪、还差什么。

> 模块级文档见 [architecture/](architecture/overview.md)；运维见 [operations/runbook.md](operations/runbook.md)。
> 字段契约见 [datasets/schema.md](datasets/schema.md)；关键决策见 [ADR](adr/)。

---

## 1. 评价镜头

引擎本身不产生 alpha——那是下游因子与策略的事。引擎决定的是下游三件事的可信度：

| 维度 | 含义 | 失败形态 |
|------|------|----------|
| **回测结论可信** | 数据正确 + PIT 无前视 + universe 无幸存者偏差 | 假收益/断裂因子 → 回测好看但实盘亏钱 |
| **信号及时** | 每交易日收盘后数据按时新鲜 | 漏更一天 = 下游节奏被打断 |
| **结果可追溯** | 口径可重算、来源可审计 | 数字对不上却查不出为什么 |

强因子不等于赚钱策略；引擎侧能做且必须做的，是保证「迭代所依据的数据不说谎」。

---

## 2. 设计分层

现有代码覆盖 1–5 层；第 6 层（运行保障）已有 `scripts/` 落地（launchd/cron、健康通知、meta 备份），见 [运维 Runbook](operations/runbook.md)。

```
┌─────────────────────────────────────────────────────────────┐
│ 下游：选股/因子项目 / DuckDB / Polars 直读                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ load() / DuckDB / Parquet 直读
┌──────────────────────────▼──────────────────────────────────┐
│ 5. 消费契约层  query/          load() API、DuckDB 视图、PIT   │
├─────────────────────────────────────────────────────────────┤
│ 4. 质量保障层  quality/        run 级 findings、湖级 health、  │
│                                跨源 diff、跨数据集对账          │
├─────────────────────────────────────────────────────────────┤
│ 3. 湖存储层    storage/ derive/  staging→curated→derived→meta │
├─────────────────────────────────────────────────────────────┤
│ 2. 采集编排层  orchestrator/ steps/  Wave DAG、manifest、      │
│                                水位、compact 门禁、worker pool │
├─────────────────────────────────────────────────────────────┤
│ 1. 数据源适配层 adapters/      tdx/sina/eastmoney/cninfo/…    │
├─────────────────────────────────────────────────────────────┤
│ 6. 运行保障层  scripts/        调度、freshness SLO、告警、备份  │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 各层职责

| 层 | 目录 | 职责 | 关键实现 |
|----|------|------|----------|
| 1 数据源适配 | `adapters/` | 协议封装，只做 I/O 与格式转换 | `tdx_protocol/`、`eastmoney/`、`sina/`、`cninfo/`、`macro/`；限速 `domain/rate_limit.py` |
| 2 采集编排 | `orchestrator/` + `steps/` | Wave DAG、批级 manifest、增量水位、失败不推水位、retry | `JobEngine`、`manifest.py`、`compact_gate.py`、`storage/state.py` |
| 3 湖存储 | `storage/` + `derive/` | staging → curated → derived → meta | `parquet.py`（写前校验 + 原子写）、`derive/adj_factors.py`；[ADR-0002](adr/0002-parquet-lake-over-database.md)/[ADR-0003](adr/0003-canonical-curated-with-source-snapshots.md) |
| 4 质量保障 | `quality/` | findings、湖级健康、跨源 diff、mock 拦截 | `audit.py::lake_health()`、`cross_checks.py`、`source_diff.py` |
| 5 消费契约 | `query/` | `load()`、DuckDB 视图 | `reader.py`、`views.py`；[ADR-0004](adr/0004-store-hfq-derive-qfq-at-query.md) |
| 6 运行保障 | `scripts/` | 调度、SLO、告警、备份 | `daily_pipeline.sh`、`health_notify.sh`、`backup_meta.sh` |

### 2.2 一次日更路径

```
收盘后
  → 调度触发（launchd/cron 或手动）
  → Wave: reference → corp_actions → daily_bars → index_bars …
      每 step：adapter → validate_dataframe → staging
  → finalize: compact（manifest 门禁）→ derive_adj_factors → audit
  → curated/derived 就绪，meta/state 水位前移
  → 下游按水位刷新缓存 / 重算因子
```

失败路径：batch failed → 水位不动 → `sde retry --run-id` 只重跑失败 batch → 成功后自动 compact→derive→audit。

---

## 3. 消费契约（引擎对外边界）

下游唯一推荐入口是 `stock_data_engine.query.load()`：

```python
load(dataset, *, start, end,
     adjust=None | "qfq" | "hfq",   # hfq 存储、qfq 按窗口 anchor 派生（ADR-0004）
     universe=None | "all_a",       # 上市/退市 + ST/停牌（后者仅 trading_status 覆盖日）
     as_of=...,                     # PIT：announce_date <= as_of
     symbols=..., items=...,
     strict_adj=False)              # True 时缺因子 fail-loud
```

| 条款 | 内容 |
|------|------|
| 研究路径偏好 hfq | qfq 的 anchor 随窗口漂移，研究场景优先 `adjust="hfq"` |
| fail-loud 复权 | `strict_adj=True` 时缺因子必须暴露，不静默 `factor=1.0` |
| PIT 双时间轴 | 财报/公告带 `announce_date`，按「截至当日已公告」对齐 |
| 水位即缓存键 | 下游缓存键宜含 `meta/state/{dataset}.json`；水位前移即失效 |
| 交易日主轴 | 窗口按 `trading_calendar` 计，不用自然日 |
| schema 只增不改 | 破坏性变更 bump `dataset_schema_version` |

影响上述条款的改动视为 breaking change。

---

## 4. 已知差距（按影响排序）

湖内证据多为 2026-07 前后实测；部分项已修复，表中保留「曾是缺口」的上下文，避免按旧印象重复投入。

### 4.1 回测结论可信

| # | 缺口 | 说明 |
|---|------|------|
| **G1** | adj_factors hfq 历史断裂 | 曾有大比例老股因子断裂；现有 append-only merge + `adj_factor_reconciliation` audit。残余多为 corporate_actions 缺事件 |
| **G2** | snapshot 类缺历史 | valuation 等曾只有当日快照；`valuation_metrics` 已可通过 baostock 历史回填 |
| **G3** | trading_status 历史 ST 缺失 | 日更只抓当天；历史区间 `universe="all_a"` 不剔除历史 ST（audit 会报覆盖起点） |
| G6a | northbound 口径收紧 | 2024-08 后北向多为季频，逐日策略需先做口径预验证 |
| G6b | index_bars 覆盖 | 核心指数覆盖已基本对齐交易日历；新指数成立日前无 bar 属正常 |

### 4.2 信号及时

| # | 缺口 | 说明 |
|---|------|------|
| **G4** | 调度/监控 | 已有 `daily_pipeline.sh` + launchd/cron + `health_notify.sh`；海外网络下东财组仍可能 soft-fail |

### 4.3 结果可追溯

| # | 缺口 | 说明 |
|---|------|------|
| **G5** | 收益率级护栏 | `adj_close_discontinuity` / `missing_corporate_action` 已接入 `sde audit --full` |
| G7 | 运维韧性 | meta 备份脚本已有；snapshot 目录增长与部分 lazy scan 仍可继续打磨 |

### 4.4 已解决、不再当缺口

- trading_calendar / index_bars / daily_bars 可回填至 2016
- financial_statement_items 带 `announce_date` PIT 轴
- 分组运行会 compact→audit，数据进 curated
- CDR（689 段）与场内 ETF 不进 `all_a`

---

## 5. 设计原则（摘要）

1. **正确性优先于覆盖面** — 假数据伤害大于缺数据
2. **fail-loud** — 不静默降级/截断/兜底
3. **防线放在引擎侧** — audit 先于下游自检发现
4. **口径可重算** — 未复权 + 独立因子、PIT 双时间轴（ADR-0003/0004）
5. **单人可运维** — 自研编排、Parquet、launchd/cron、SQLite manifest

更细条目见 [architecture/design-principles.md](architecture/design-principles.md)。
