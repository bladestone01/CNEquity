# ashare-lake 架构设计（分层 + 差距分析）

定位：系统怎么分层、边界在哪、还差什么。

> 模块级文档见 [architecture/](architecture/overview.md)；运维见 [operations/runbook.md](operations/runbook.md)。
> 字段契约见 [datasets/schema.md](datasets/schema.md)；关键决策见 [ADR](adr/)。

---

## 1. 引擎在管什么

引擎本身不产生 alpha。它主要影响三件事：回测用的数据是否干净（PIT、universe、复权别搞砸）、日更是否按时到、数字对不上时能不能追到源。脏数据会让回测很好看、实盘很惨——这是引擎侧该挡住的。

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

失败路径：batch failed → 水位不动 → `asl retry --run-id` 只重跑失败 batch → 成功后自动 compact→derive→audit。

---

## 3. 消费契约（引擎对外边界）

下游唯一推荐入口是 `ashare_lake.query.load()`：

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

## 4. 已知限制与现状

几点用之前心里有数（不少是踩过坑之后的现状，不是待办清单）：

- **复权因子**：老股 hfq 曾大面积断裂；现在是 append-only merge，加上 `adj_factor_reconciliation` audit。残余多为 `corporate_actions` 缺事件。
- **估值历史**：以前不少是当日快照；`valuation_metrics` 已可用 baostock 回填。
- **ST / 停牌**：日更只抓当天，更早窗口 `universe="all_a"` 不会按历史 ST 剔除（audit 会报覆盖起点）。
- **北向**：2024-08 后多为季频，别当逐日序列用。
- **调度**：`daily_pipeline.sh` + cron/launchd + `health_notify.sh` 已有；海外网络下东财组仍可能 soft-fail。
- **运维**：meta 备份有了；snapshot 目录增长和部分 lazy scan 还能再收。

已经比较稳的：日历 / 指数 / 日线可回填到 2016；财报带 `announce_date` PIT；分组跑完会 compact→audit；CDR（689）和场内 ETF 不进 `all_a`。

原则摘要见 [design-principles](architecture/design-principles.md)：源失败就暴露、口径可重算、audit 放引擎侧、组件保持单人可运维。
