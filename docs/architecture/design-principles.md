# 设计原则

StockDataEngine 的一切设计取舍，服务于下游三件事：**回测可信**、**信号及时**、**结果可追溯**。详见 [architecture.md](../architecture.md) §1。

---

## 1. 永不伪造（Fail-Loud）

- 数据源失败 → batch `failed`，不静默返回空 DataFrame 或假数据
- 测试唯一例外：`[tdx_protocol].allow_mock=true`，且行必须标 `source="mock"`；audit 会拦截
- 分页截断、限速失败、schema 不匹配 → 显式失败，不截断后当成功

**教训**：静默兜底会直接污染选股结论。

---

## 2. 可溯源（Provenance）

每个 curated 行携带：

| 列 | 含义 |
|----|------|
| `source` | adapter 名称（如 `tdx_protocol`、`eastmoney`） |
| `data_version` | 源侧版本或抓取批次标识 |
| `fetched_at` | UTC 抓取时间戳 |

下游可审计「这一行何时、从哪来」。

---

## 3. 口径可重算

| 场景 | 做法 |
|------|------|
| 复权 | 存**未复权**价 + 独立 `adj_factors`；qfq/hfq 查询期组合（ADR-0004） |
| 多源 | curated 每 PK 一行；备源进 snapshot，diff 由 audit 产出（ADR-0003） |
| PIT | 低频数据双时间轴：`report_period` + `announce_date` |
| 派生 | `derived/` 或 DuckDB 视图可从 curated 完全重算 |

---

## 4. 正确性优先于覆盖面

一个新数据集若口径未验证，不如不交付。已知正确性缺陷须先于新功能修复。

优先级示例：

1. compact 门禁与水位一致性
2. instruments 合并保留退市股
3. adj_factors 断裂与 append-only 语义

---

## 5. 防线放在引擎侧

能在 `sde audit --full` 发现的问题，不应只依赖下游项目自检。例如：

- 复权收益极值扫描（`adj_close_discontinuity`）
- adj_factors × corporate_actions 对账
- PK 重复、mock 行、分区行数突变

---

## 6. 单人可运维

刻意选择可本地理解、可重建的组件：

- 自研编排（非 Airflow）
- Parquet 湖（非 PostgreSQL）
- launchd/cron（非 K8s）
- SQLite manifest（非分布式协调）

目标：一个人能读完全部代码并排障。

---

## 7. Schema 只增不改

- curated 列语义稳定；破坏性变更需版本 bump + 迁移说明
- `domain/schemas.py` 写前强校验；`domain/datasets.py` 注册表与 schema 测试同步

---

## 8. 无前视偏差（PIT）

- `load(..., as_of=)` 对 PIT 数据集过滤 `announce_date <= as_of`
- 不得用 `report_period` 代替公告日做时点对齐

---

## 9. Universe 诚实

`universe="all_a"` 的 ST/停牌过滤**仅限** `trading_status` 有数据的日期。历史无覆盖区间只做上市/退市过滤，并在 audit 中报告覆盖起点 — 不假装已剔除历史 ST。

---

## ADR 索引

| 编号 | 标题 |
|------|------|
| [0001](../adr/0001-record-architecture-decisions.md) | 用 Markdown 记录架构决策 |
| [0002](../adr/0002-parquet-lake-over-database.md) | Parquet 湖优于数据库 |
| [0003](../adr/0003-canonical-curated-with-source-snapshots.md) | Canonical + 备源快照 |
| [0004](../adr/0004-store-hfq-derive-qfq-at-query.md) | 存 hfq、查询派生 qfq |

---

## 相关文档

- [架构总览](overview.md)
- [Schema 契约](../datasets/schema.md)
