# quality 模块

路径：`src/ashare_lake/quality/`

数据质量保障：run 级审计、湖级健康、跨数据集对账、主备源 diff、failover 快照写入。

---

## 文件一览

| 文件 | 职责 |
|------|------|
| `audit.py` | `run_audit()`, `lake_health()` |
| `dataset_checks.py` | PK 重复、mock、空集、行数突变 |
| `cross_checks.py` | bars×calendar、valuation×bars、adj 对账、ST 标签对照 |
| `macro_checks.py` | 宏观月度序列的陈旧检测与修订留痕 |
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
| `mixed_partition_granularity` | error（盘上分区粒度与注册表不一致；跨粒度会让同一 PK 出现两次） |
| `source="mock"` 且非测试 | error |
| 分区行数相对上次 run 突变 | warning |
| `partition_fragmentation` | warning（分区过细，几乎全是 footer） |
| 空数据集（预期非空） | warning |

---

## cross_checks.py

| 检查 | 说明 |
|------|------|
| daily_bars vs trading_calendar | 交易日无 bar |
| valuation vs daily_bars | 估值有、行情无 |
| adj_factor_reconciliation | bar-to-bar 复权收益 > 阈值；除权日缺 corporate_actions |
| `st_label_crosscheck` | `trading_status` 的 ST 标签 vs `instruments` 简称里的 ST 前缀 |

### st_label_crosscheck

两侧都已在 curated 里，**不产生任何请求**。

这是一个真正独立的对照：ST 简称由交易所指定，经 **TDX 二进制协议**进入
`instruments`；风险警示板名单经 **东财 HTTP** 进入 `trading_status`。不同厂商、
不同协议、同一个交易所事实。已移除的 AkShare ST 并集只是看起来独立——它查的是
和东财适配器完全相同的 push2 端点与 `fs` 过滤条件，永远不可能给出不同答案
（[issue #3](https://github.com/rootSunc/ashare-lake/issues/3)）。

容差 `ST_CROSSCHECK_MAX_DISAGREEMENT = 3`：改名当天两个 step 分别抓取，
个位数的边界名单属于正常抖动。

2026-08-01 实测：两侧各 205 个，**对称差 0**。

---

## macro_checks.py

| 检查 | 严重度 | 说明 |
|------|--------|------|
| `macro_indicator_stale` | warning | 月度指标最新观测距运行日超过阈值 |
| `macro_value_revised` | warning / info | 已入湖的 `(indicator_id, obs_date)` 数值被改写 |

### 为什么需要 macro_indicator_stale

月度序列每次运行都重抓全量、按 `(indicator_id, obs_date)` 去重，所以
**一个停止发布的源和一个健康的源在 curated 里长得一模一样**——旧行都还在，
没有任何 step 会失败。只有「最新观测距今多远」能暴露它。

阈值按各指标实测发布节奏 + 约 1.5 个月余量设定（`MONTHLY_STALE_DAYS`），
即错过大约一个发布周期才告警。2026-08-01 实测余量：

| indicator | 最新观测 | 滞后 | 阈值 | 余量 |
|-----------|----------|------|------|------|
| `pmi_manufacturing` | 2026-07-31 | 1d | 45d | 44d |
| `m2_yoy` | 2026-06-30 | 32d | 75d | 43d |
| `social_financing` | 2026-06-30 | 32d | 75d | 43d |

`m2_yoy` 与 `social_financing` 同为央行月中发布，所以阈值相同。

> 这条检查的动机就来自一次实测：社融原先读商务部转载，落后两个发布周期且带着
> 修订前的旧值，而**湖里看不出任何异常**——旧行都在，没有 step 失败。换直连央行
> 之后滞后回到 32 天，但检查保留：下一次某个源静默停更时，只有它会说话。
> 见 [pboc 适配器](adapters/pboc.md)。

### 为什么修订是「留痕」而不是「阻止」

compact 按主键保留最新 `fetched_at`，所以发布方修订某个月时旧值会被覆盖且不可恢复。

这个覆盖行为是**要保留的**——正是它让 #3 里错误的 `m2_yoy` 历史在下次运行时自愈，
不需要迁移脚本。所以检查放在 step 里、写入之前：比对增量与 curated，把变化记进
findings，然后照常写入。curated 仍然只持有最新发布值，findings 是旧值存在过的唯一记录。

判定：相对变化 > `REVISION_MATERIAL_RELATIVE`（5%）记 warning，否则 info。

---

## source_diff.py

读取 `meta/source_snapshots/` 与 curated 抽样比对：

- 价格类：`price_tolerance_bps`（默认 10bps）
- 输出 `meta/quality/source_diffs/{run_id}.json`

**同 PK 不自动换源**（ADR-0003 **switching**）。**不相交 key 的路由**（BJ→sina、tip TDX 缺口→东财 clist）见 [ADR-0005](../adr/0005-source-routing-vs-switching.md)。

---

## failover.py

配置驱动（`[failover.datasets]`）：

1. 主源 batch 失败（或 tip 缺 key）
2. **tip 日**：一次 push2 clist → 只把缺失 key 写入 staging（`source=eastmoney`），并写 snapshot
3. **多日窗口**：对失败 symbol 走 per-symbol kline → staging + snapshot
4. audit 阶段 `source_diff` 仍比对 primary vs snapshot

---

## 相关文档

- [设计原则 — 防线在引擎侧](../architecture/design-principles.md)
- [ADR-0005 routing vs switching](../adr/0005-source-routing-vs-switching.md)
- [故障排查](../operations/troubleshooting.md)
