# CLI 参考

命令：`sde`（`stock_data_engine.cli.main:cli`）

全局默认：`--config configs/stockdata.toml`

---

## sde init

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

## sde config validate

校验 TOML 与 step 引用。有错退出 1。

---

## sde run daily

| 选项 | 说明 |
|------|------|
| `--group` | `core` \| `capital` \| `signals` \| `fundamentals` \| `macro_risk` \| `research` |
| `--backfill` | 强制 backfill 语义（慎用） |

无 `--group` 时跑完整 `[job.daily.waves]` DAG。

成功或 `skipped_non_trading_day` 退出 0。

---

## sde backfill \<dataset\>

单数据集 backfill。snapshot 且无 `backfill_source` 时拒绝。

成功时自动 compact 当前 run。

---

## sde compact

| 选项 | 说明 |
|------|------|
| `--run-id` | 指定 run（默认最近 run） |

将 staging 合并入 curated。

---

## sde derive [name]

| name | 说明 |
|------|------|
| `adj_factors`（默认） | 计算 Sina hfq 因子 |
| `trading_status` | 派生历史停牌记录 |

---

## sde audit

| 选项 | 说明 |
|------|------|
| `--run-id` | 指定 run 的 findings（默认最近 run） |
| `--full` | 湖级健康快照（非 per-run 文件） |

`--full` 且 UNHEALTHY 退出 1。

---

## sde status

| 选项 | 说明 |
|------|------|
| `--datasets` | 逐数据集新鲜度表；有 STALE 退出 1 |

无选项：输出最近 run 的 JSON 摘要。

---

## sde retry --run-id \<id\>

重试失败 batch / 补 init 缺失 step。init run 走 `resume_init`。

成功退出 0；`RunLockError` 报错退出。

---

## sde clean

| 选项 | 说明 |
|------|------|
| `--dry-run` | 仅报告可删 staging |
| `--orphan-retention-days` | 无 manifest 的 orphan 保留天数（默认 7） |
| `--force` | 也删 failed run 的 staging（retry 将全量重抓） |

---

## sde catalog

JSON 列出 curated 各数据集文件数与行数。

---

## sde query

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

## sde servers test

测试 TDX 连接。无 mootdx 时提示安装 `[tdx]`。

---

## sde --version

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
