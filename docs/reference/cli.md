# CLI 参考

命令：`asl`（`ashare_lake.cli.main:cli`）

全局默认：`--config configs/ashare-lake.toml`

---

## asl demo

一分钟真源小样（涨星 / 上手用）。拉少量流动性股票的近期日线，**不是**全市场 `asl init`。

| 选项 | 说明 |
|------|------|
| `--symbols` | 逗号分隔标的（默认茅台/平安银行/五粮液/宁德/中国平安） |
| `--days` | 约多少个交易日的 `daily_bars`（默认 30） |
| `--data-root` | 独立湖根目录（默认 `data/ashare-lake-demo`） |
| `--trade-date` | 截至日 YYYY-MM-DD（默认今天 / 最近交易日） |
| `--config-out` | 写出供后续 `asl query` 使用的小配置（默认 `configs/ashare-lake.demo.toml`） |

流程：建目录 → 探测 TDX → 拉 instruments 并裁成 demo 宇宙 → 交易日历 → `daily_bars` + compact → 打印样例表。终端有分阶段进度与 INFO 日志。需要能访问 TDX；`allow_mock` 不会打开。

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

## asl delisted

重建退市宇宙（幸存者偏差修复）。

| 子命令 | 说明 |
|--------|------|
| `discover [--limit N]` | 扫 issued code space，分类为曾上市 / 从未发行（可续跑） |
| `status [--since]` | 目录摘要：数量、年份、尚未 ingest |
| `repair [--since]` | **不重新拉行情**：用已有 `daily_bars` 跨度写 `instruments.delist_date`，并清掉 `认购款` 占位 |
| `backfill [--since]` | 对目录中尚未有行情的退市股拉 Sina 历史并 compact |

推荐顺序：`discover` → `repair`（bars 已在湖里时）→ `backfill`（补缺口）。

```bash
asl delisted status
asl delisted repair
asl delisted backfill --since 2016-01-01
asl delisted discover --limit 500   # 扩大 band 后的续扫
```

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
| `trading_status` | 派生历史停牌记录（`--start` / `--end` 按年分块重建） |
| `sector_routing` | 可选：EM 板块 × TDX 88xxxx 名称映射表（**不驱动** sector_bars 采集） |
| `sector_code_map` | BK* ↔ BOARD_CODE 身份映射（lake-only；推荐成分 join） |

```bash
asl derive trading_status --start 2001-01-01 --end 2001-12-31
```

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

删除已 compact 的终态 run staging，以及超龄 orphan。终态含 `success` / `warning` / `failed`（需 incomplete=0 且有成功 compact batch）。

| 选项 | 说明 |
|------|------|
| `--dry-run` | 仅报告可删 staging |
| `--orphan-retention-days` | 无 manifest 的 orphan 保留天数（默认 7） |
| `--force` | 也删尚未 cleanup-ready 的 staging（incomplete / 未 compact）；成功 fetch batch 会被 demote，`asl retry` 全量重抓。**不要**对 success-without-compact 用 force——先 `asl compact --run-id` |

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
