# quality 模块

路径：`src/ashare_lake/quality/`

数据质量保障：run 级审计、湖级健康、跨数据集对账、主备源 diff、failover 快照写入。

---

## 文件一览

| 文件 | 职责 |
|------|------|
| `audit.py` | `run_audit()`, `lake_health()` |
| `dataset_checks.py` | PK 重复、mock、空集、行数突变 |
| `cross_checks.py` | bars×calendar、valuation×bars、adj 对账 |
| `source_diff.py` | 主源 vs snapshot 字段 diff |
| `failover.py` | 主源失败时写 `source_snapshots` |

---

## audit.py

### run_audit(cfg, run_id, trade_date) → int

写入 `meta/quality/findings/{run_id}.json`：

- 各数据集 `dataset_checks`
- `cross_checks`（若依赖数据集已 compact）
- `source_diff`（若配置了 failover）
- 上下文 findings（compact 跳过、derive 警告）

返回 findings 条数。

### lake_health(cfg, anchor_date) → dict

`asl audit --full` 使用。检查：

| 项 | 说明 |
|----|------|
| `empty_datasets` | 无 parquet 的注册数据集 |
| `stale_datasets` | 水位落后超过 `max_staleness_days` |
| `findings_by_severity` | error / warning / info 计数 |
| `adj_factor_reconciliation` | 复权收益极值 + 缺 corporate_actions |
| `healthy` | 无 error 级 finding |

---

## dataset_checks.py

| 检查 | 严重度 |
|------|--------|
| PK 重复 | error |
| `source="mock"` 且非测试 | error |
| 分区行数相对上次 run 突变 | warning |
| 空数据集（预期非空） | warning |

---

## cross_checks.py

| 检查 | 说明 |
|------|------|
| daily_bars vs trading_calendar | 交易日无 bar |
| valuation vs daily_bars | 估值有、行情无 |
| adj_factor_reconciliation | bar-to-bar 复权收益 > 阈值；除权日缺 corporate_actions |

---

## source_diff.py

读取 `meta/source_snapshots/` 与 curated 抽样比对：

- 价格类：`price_tolerance_bps`（默认 10bps）
- 输出 `meta/quality/source_diffs/{run_id}.json`

**不自动修正 curated**（ADR-0003）。

---

## failover.py

配置驱动（`[failover.datasets]`）：

1. 主源 batch 失败
2. 调备源 adapter 拉取
3. 写 snapshot（带 provenance）
4. audit 阶段 diff

---

## 相关文档

- [设计原则 — 防线在引擎侧](../architecture/design-principles.md)
- [故障排查](../operations/troubleshooting.md)
