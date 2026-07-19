# 架构总览

StockDataEngine 是 A 股数据的**采集编排层**：在多个外部数据源之上，通过自研 Wave 引擎并行拉取、校验、落湖，并以稳定 schema 交付给下游选股/因子项目。

完整差距分析与实盘可信度评价见 [architecture.md](../architecture.md)（v1.0）。

---

## 六层设计

```
┌─────────────────────────────────────────────────────────────┐
│ 下游：选股/因子项目 / DuckDB / Polars 直读                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ load() / SQL / Parquet
┌──────────────────────────▼──────────────────────────────────┐
│ 5. 消费契约层  query/          load()、DuckDB 视图、PIT、复权    │
├─────────────────────────────────────────────────────────────┤
│ 4. 质量保障层  quality/        audit、cross_checks、source_diff│
├─────────────────────────────────────────────────────────────┤
│ 3. 湖存储层    storage/ derive/  staging→curated→derived→meta │
├─────────────────────────────────────────────────────────────┤
│ 2. 采集编排层  orchestrator/ steps/  Wave DAG、manifest、worker │
├─────────────────────────────────────────────────────────────┤
│ 1. 数据源适配层 adapters/      薄 I/O，不含业务编排            │
├─────────────────────────────────────────────────────────────┤
│ 6. 运行保障层  scripts/        launchd、cron、备份、告警          │
└─────────────────────────────────────────────────────────────┘
```

| 层 | 目录 | 职责 |
|----|------|------|
| 1 | `adapters/` | 协议封装、分页、源侧格式转换 |
| 2 | `orchestrator/` + `steps/` | Job/Wave/Step 执行、批级 manifest、增量水位 |
| 3 | `storage/` + `derive/` | Parquet 四层湖、compact、派生 |
| 4 | `quality/` | run 级与湖级健康检查 |
| 5 | `query/` | 读取 API 与 DuckDB 视图 |
| 6 | `scripts/` | 调度、备份、通知（见 [运维 Runbook](../operations/runbook.md)） |

---

## 编排模型

```
Job (daily / init / backfill / retry)
  └── Wave(s) — 配置中的并行/串行边界
        └── Step level(s) — 拓扑排序后的依赖层
              └── Task / Batch — worker step 的 symbol-batch 粒度
                    └── Manifest (SQLite) — runs + batches 生命周期
```

- **Step**：`@register_step` 注册的可执行单元（28 个：25 采集 + 3 finalize）
- **Batch**：`daily_bars` 等多进程 step 的最小重试单位
- **水位**：`meta/state/{dataset}.json`，compact 成功后前移；有 failed batch 的数据集不推水位

---

## 与下游的契约边界

下游应通过 `stock_data_engine.query.load()` 读数。核心条款：

| 条款 | 说明 |
|------|------|
| hfq 存储 | 湖内只存后复权因子；qfq 查询期按窗口 anchor 派生（[ADR-0004](../adr/0004-store-hfq-derive-qfq-at-query.md)） |
| fail-loud 复权 | `strict_adj=True` 时缺因子报错，不静默 `factor=1.0` |
| PIT | `financial_statement_items`、`announcement_index` 按 `announce_date <= as_of` |
| universe | `all_a` = 上市/退市过滤 + trading_status 覆盖日内的 ST/停牌过滤 |
| schema 演进 | curated 列只增不改；破坏性变更 bump `dataset_schema_version` |

---

## 技术栈

| 组件 | 选型 |
|------|------|
| Python | ≥ 3.11 |
| DataFrame | Polars |
| 存储 | Parquet (PyArrow, zstd) |
| 查询 | DuckDB（视图层） |
| 编排元数据 | SQLite WAL (`manifest.db`) |
| CLI | Click |
| TDX | mootdx（可选 extra） |

---

## 关键架构决策（ADR）

| ADR | 决策 |
|-----|------|
| [0002](../adr/0002-parquet-lake-over-database.md) | Parquet 湖优于自建数据库 |
| [0003](../adr/0003-canonical-curated-with-source-snapshots.md) | curated 每 PK 一行；备源进 snapshot，不自动切源 |
| [0004](../adr/0004-store-hfq-derive-qfq-at-query.md) | 只存 hfq 因子，qfq 查询期计算 |

---

## 相关文档

- [数据流](data-flow.md)
- [数据湖布局](lake-layout.md)
- [设计原则](design-principles.md)
- [模块索引](../modules/README.md)
