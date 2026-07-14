# StockDataEngine 运维 Runbook（Phase B 运行保障）

> **已迁移**：完整运维文档见 [operations/runbook.md](operations/runbook.md)，并配套 [脚本说明](operations/scripts.md)、[故障排查](operations/troubleshooting.md)。

面向自用实盘：`wb daily` 上线后引擎是生产依赖，本文覆盖每日无人值守运行、失败告警、备份恢复。对应 [roadmap.md](roadmap.md) Phase B、[architecture.md](architecture.md) 第 6 层（运行保障）。

## 组件一览

| 层 | 脚本 | 作用 |
| --- | --- | --- |
| 调度 (B1) | `scripts/daily_pipeline.sh` | 每交易日按 schedule_groups 顺序跑全部数据组，末尾串联健康检查 + 备份 |
| 调度 (B1) | `scripts/install_scheduler.sh` / `uninstall_scheduler.sh` | 安装 / 卸载 launchd 定时任务 |
| 告警 (B2) | `scripts/health_notify.sh` | 跑 `sde audit --full` + `sde status --datasets`，异常弹 macOS 通知并非零退出 |
| 备份 (B3) | `scripts/backup_meta.sh` | 快照 `manifest.db` + `meta/state`（+ `quality`），tar 轮换 |

所有脚本用仓库自带 `.venv/bin/sde`，路径自解析，可独立运行也可被 pipeline 串联。

## 安装调度

```bash
cd /Users/chaosun/code/StockDataEngine
scripts/install_scheduler.sh
```

- 在 `~/Library/LaunchAgents/com.stockdataengine.daily.plist` 生成并加载 launchd 任务。
- 触发时刻：**每天本地 16:05**（A 股 15:00 收盘后落定）。非交易日 `sde run daily` 自动跳过（`skipped_non_trading_day`，退出 0），成本极低。
- 重复运行安装脚本即更新（先 unload 再 load，幂等）。

验证 / 手动触发 / 卸载：

```bash
launchctl list | grep stockdataengine          # 确认已加载
launchctl start com.stockdataengine.daily      # 立即跑一次（首装建议手动验一次）
scripts/uninstall_scheduler.sh                 # 卸载
```

> Linux 无 launchd，用 cron：`5 16 * * *  <repo>/scripts/daily_pipeline.sh`

## 每日执行流程

`daily_pipeline.sh` 顺序（**串行**，因 `workers=1` + mootdx 非 fork-safe）：

```
core → capital → signals → fundamentals → macro_risk → research
  → health_notify.sh（健康门禁 + 通知）
  → backup_meta.sh（元数据快照）
```

- 单个数据组失败**不中断**后续组（尽量多拿当天数据），但 pipeline 最终以非零退出，且健康检查会通知。
- 组顺序对应 `configs/stockdata.toml [job.daily.groups]` 的错峰语义（此处是执行顺序，非按墙钟时间）。

## 日志

均在 `data/stock-data-engine/logs/`（gitignored）：

| 文件 | 内容 |
| --- | --- |
| `daily-YYYYMMDD.log` | pipeline 主流水 + 各组输出 |
| `health-YYYYMMDD.log` | 每次健康检查的 audit / status 全文 |
| `launchd.out.log` / `launchd.err.log` | launchd 捕获的 stdout/stderr |

## 失败处置

1. 收到「StockDataEngine 数据异常」通知或日更失败 → 看当天 `daily-*.log` 定位失败组。
2. 手动重跑单组：`.venv/bin/sde run daily --group <组名>`。
3. 有失败 run 需重试批次：`sde status` 找 run_id → `sde retry --run-id <id>`。
4. 复核整体健康：`sde audit --full`（HEALTHY/UNHEALTHY）+ `sde status --datasets`（逐数据集新鲜度，STALE 非零退出）。

### SLO（PRD §12 可度量口径）

- **日更成功率**：连续两周无人工干预，pipeline 退出 0 的交易日占比 ≥ 99%。
- **告警时效**：注入一次失败，健康检查应在当次运行结束时（分钟级）弹通知。
- **新鲜度**：交易日 T+1 各每日数据集 `sde status --datasets` 无 STALE（季度/事件类按各自容忍度，见 `domain.datasets.is_stale`）。

## 备份与恢复

**备份对象**：`manifest.db`（运行历史/水位来源）+ `meta/state/`（各源增量水位，PIT 关键）+ `meta/quality/`。
**不备份**：`adj_factors_cache`（万级，可 `sde derive adj_factors` 重算）、`locks`（运行时）、curated parquet（可重采）。

```bash
scripts/backup_meta.sh                          # 默认写 data/.../backups/，保留 14 天
scripts/backup_meta.sh "" /Volumes/ext/sde-bak 30   # 自定义目录 + 保留天数
```

> ⚠️ 默认备份目录在数据湖内，仅防误删/误改；真做磁盘级容灾，请把第二参数（或 `SDE_BACKUP_DIR`）指到湖外（外置盘 / iCloud）。

**恢复演练**（删库→复原水位与运行历史）：

```bash
cd data/stock-data-engine/meta
tar -xzf <repo>/data/stock-data-engine/backups/meta-YYYYMMDD-HHMMSS.tar.gz
# 覆盖 manifest.db 与 state/ 后，`sde status` 应恢复水位；再跑一次日更即增量续采。
```

## 环境变量（脚本可调）

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `SDE_CONFIG` | `configs/stockdata.toml` | 配置路径 |
| `SDE_LOG_DIR` | `data/.../logs` | 日志目录 |
| `SDE_GROUPS` | 全部 6 组 | 覆盖要跑的组（空格分隔） |
| `SDE_NOTIFY` | `1` | 设 `0` 关闭桌面通知 |
| `SDE_BACKUP_DIR` | `data/.../backups` | 备份目录 |
| `SDE_BACKUP_RETENTION_DAYS` | `14` | 备份保留天数 |
