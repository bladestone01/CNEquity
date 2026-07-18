# 运维 Runbook

面向生产日更：调度、健康门禁、备份恢复与 SLO。对应 [路线图 Phase B](../roadmap.md) 与 [架构第 6 层](../architecture/overview.md)。

> 历史路径 `docs/ops-runbook.md` 保留并重定向至本文。

---

## 组件一览

| 能力 | 脚本 | 作用 |
|------|------|------|
| 调度 B1 | `scripts/daily_pipeline.sh` | 串行跑 6 个 schedule group + 健康检查 + 备份 |
| 调度 B1 | `scripts/install_scheduler.sh` | 安装 macOS launchd（每天 16:05） |
| 调度 B1 | `scripts/uninstall_scheduler.sh` | 卸载 launchd |
| 告警 B2 | `scripts/health_notify.sh` | `audit --full` + `status --datasets` + macOS 通知 |
| 备份 B3 | `scripts/backup_meta.sh` | manifest + state + quality 的 tar 轮换 |

脚本使用仓库 `.venv/bin/sde`，路径相对仓库根目录自解析。

---

## 安装调度

```bash
cd /path/to/StockDataEngine
scripts/install_scheduler.sh
```

- 生成 `~/Library/LaunchAgents/com.stockdataengine.daily.plist`
- **每天本地 16:05** 触发（收盘后）
- 非交易日自动跳过（退出 0）
- **漏跑 / 周末补数**：`uv run sde run catchup`（门禁 core + breadth），或
  `scripts/daily_pipeline.sh YYYY-MM-DD` / `SDE_TRADE_DATE=...`（全组定点）

```bash
launchctl list | grep stockdataengine
launchctl start com.stockdataengine.daily   # 手动触发
scripts/uninstall_scheduler.sh
```

**Linux cron**：

```cron
5 16 * * * /path/to/StockDataEngine/scripts/daily_pipeline.sh
```

---

## 每日 Pipeline

```
core → capital → signals → fundamentals → macro_risk → research
  → health_notify.sh
  → backup_meta.sh
```

- 单组失败不中断后续组（尽量多采数据）
- Pipeline 最终退出码反映整体成败
- 生产 `daily_pipeline.sh` 常设 `workers=1`（mootdx 与多进程兼容性）

组与 step 映射见 [配置 — 调度组](../getting-started/configuration.md#调度组)。

---

## 日志

目录：`{data.root}/logs/`

| 文件 | 内容 |
|------|------|
| `daily-YYYYMMDD.log` | 各组 sde 输出 |
| `health-YYYYMMDD.log` | audit / status 全文 |
| `launchd.out.log` / `launchd.err.log` | launchd 标准流 |

---

## 日常巡检命令

```bash
sde status --datasets          # 新鲜度；STALE 时退出 1
sde audit --full               # 湖级健康；UNHEALTHY 退出 1
sde catalog                    # 行数概览
```

---

## 失败处置

1. 查看 `daily-*.log` 定位失败组
2. 重跑单组：`sde run daily --group <name>`
3. 批级失败：`sde status` → `sde retry --run-id <id>`
4. 复核：`sde audit --full` + `sde status --datasets`

详见 [故障排查](troubleshooting.md)。

---

## SLO

| 指标 | 目标 |
|------|------|
| 日更成功率 | 两周内 ≥99% 交易日 pipeline 退出 0 |
| 告警时效 | 失败当次 run 结束分钟内通知 |
| 新鲜度 | T+1 `status --datasets` 无 STALE（季频数据集按 `max_staleness_days`） |

---

## 备份与恢复

**备份**：`manifest.db` + `meta/state/` + `meta/quality/`

**不备份**：`adj_factors_cache`（可 derive 重算）、`locks`、curated parquet（可重采）

```bash
scripts/backup_meta.sh
scripts/backup_meta.sh "" /Volumes/ext/sde-bak 30
```

**恢复**：

```bash
cd data/stock-data-engine/meta
tar -xzf ../backups/meta-YYYYMMDD-HHMMSS.tar.gz
sde status    # 确认水位恢复
sde run daily --group core   # 增量续采
```

⚠️ 默认备份在湖内，磁盘级容灾请将 `SDE_BACKUP_DIR` 指到湖外。

---

## 环境变量

| 变量 | 默认 | 作用 |
|------|------|------|
| `SDE_CONFIG` | `configs/stockdata.toml` | 配置 |
| `SDE_LOG_DIR` | `{data.root}/logs` | 日志 |
| `SDE_GROUPS` | 全部 6 组 | 覆盖 pipeline 组列表 |
| `SDE_NOTIFY` | `1` | `0` 关闭通知 |
| `SDE_BACKUP_DIR` | 湖内 backups | 备份目录 |
| `SDE_BACKUP_RETENTION_DAYS` | 14 | 保留天数 |

---

## 相关文档

- [脚本说明](scripts.md)
- [故障排查](troubleshooting.md)
- [PRD 附录 C](../PRD.md)
