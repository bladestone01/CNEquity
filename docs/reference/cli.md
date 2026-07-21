# CLI 参考

命令：`asl`（`ashare_lake.cli.main:cli`）

全局默认：`--config configs/ashare-lake.toml`

---

## asl init

初始化数据湖并执行 init phases。

| 选项 | 说明 |
|------|------|
| `--config` | 配置文件路径 |
| `--layout-only` | 仅建目录、manifest、DuckDB 视图 |
| `--trade-date YYYY-MM-DD` | init 截至交易日（默认今天） |
| `--resume` | 续跑最近未完成 init |
| `--run-id` | 续跑指定 init run（隐含 resume） |
| `--keep-going` | phase 失败后继续后续 phase |

退出：result `status != success` 时退出 1。

---

## asl config validate

校验 TOML 与 step 引用。有错退出 1。

---

## asl run daily

| 选项 | 说明 |
|------|------|
| `--group` | `core` \| `capital` \| `signals` \| `fundamentals` \| `macro_risk` \| `research` |
| `--backfill` | 强制 backfill 语义（慎用） |

无 `--group` 时跑完整 `[job.daily.waves]` DAG。

成功或 `skipped_non_trading_day` 退出 0。

---

## asl backfill \<dataset\>

单数据集 backfill。snapshot 且无 `backfill_source` 时拒绝。

成功时自动 compact 当前 run。

### sector_bars

| 选项 | 说明 |
|------|------|
| `--retry-failed` | 跳过 checkpoint 中已完成的板块，只重试失败项 |
| `--force` | 清空 checkpoint 后全量重拉（与 `--retry-failed` 互斥） |

Checkpoint：`meta/state/sector_bars_backfill.json`。失败超过 50% 时 step 状态为 `warning` 但仍写入已成功部分。

**网络**：历史 kline 走 `push2his.eastmoney.com`，需国内或大陆出口代理；日更 clist 在海外通常可用。

```bash
# 首次或换源后全量（建议在国内机器）
asl backfill sector_bars --config configs/ashare-lake.toml --force

# 续跑失败板
asl backfill sector_bars --config configs/ashare-lake.toml --retry-failed
```

---

## asl compact

| 选项 | 说明 |
|------|------|
| `--run-id` | 指定 run（默认最近 run） |

将 staging 合并入 curated。

---

## asl repartition [dataset]

| 选项 | 说明 |
|------|------|
| `--all` | 改写所有布局与配置不一致的数据集 |
| `--dry-run` | 只报告效果，不落盘 |

把历史分区改写成 `DatasetSpec.partition_granularity` 配的周期
（见 [分区粒度](../architecture/lake-layout.md#分区粒度)）。不带参数则列出待改写的数据集。

读路径按目录形状自解析，改粒度本身**不需要**迁移；这条命令只是把碎文件收回来。
写入是先建临时目录、逐分区写完并核对总行数，再一次 rename 换上去，中途挂掉不动原数据；
重复执行是幂等的。

```bash
asl repartition --all --dry-run   # 先看影响
asl repartition trading_calendar  # 单个数据集
```

---

## asl derive [name]

| name | 说明 |
|------|------|
| `adj_factors`（默认） | 计算 Sina hfq 因子 |
| `trading_status` | 派生历史停牌记录 |
| `sector_routing` | 可选：EM 板块 × TDX 88xxxx 名称映射表（**不驱动** sector_bars 采集） |
| `sector_code_map` | BK* ↔ BOARD_CODE 身份映射（lake-only；推荐成分 join） |

---

## asl audit

| 选项 | 说明 |
|------|------|
| `--run-id` | 指定 run 的 findings（默认最近 run） |
| `--full` | 湖级健康快照（非 per-run 文件） |

`--full` 且 UNHEALTHY 退出 1。

---

## asl status

| 选项 | 说明 |
|------|------|
| `--datasets` | 逐数据集新鲜度表；有 STALE 退出 1 |

无选项：输出最近 run 的 JSON 摘要。

---

## asl retry --run-id \<id\>

重试失败 batch / 补 init 缺失 step。init run 走 `resume_init`。

成功退出 0；`RunLockError` 报错退出。

---

## asl clean

| 选项 | 说明 |
|------|------|
| `--dry-run` | 仅报告可删 staging |
| `--orphan-retention-days` | 无 manifest 的 orphan 保留天数（默认 7） |
| `--force` | 也删 failed run 的 staging（retry 将全量重抓） |

---

## asl catalog

JSON 列出 curated 各数据集文件数与行数。

---

## asl query

**DuckDB 模式**（默认）：

| 选项 | 默认 |
|------|------|
| `--sql` | `SELECT COUNT(*) AS n FROM daily_bars` |

**On-demand 模式**：

| 选项 | 说明 |
|------|------|
| `--dataset` | on-demand 数据集名 |
| `--symbol` | 如 `600519.SH` |

---

## asl servers test

测试 TDX 连接。无 mootdx 时提示安装 `[tdx]`。

---

## asl --version

包版本号。

---

## 退出码汇总

| 码 | 场景 |
|----|------|
| 0 | 成功、非交易日跳过、健康检查通过 |
| 1 | 运行失败、UNHEALTHY、STALE、校验失败 |

---

## 相关文档

- [快速开始](../getting-started/quickstart.md)
- [cli 模块](../modules/cli.md)
