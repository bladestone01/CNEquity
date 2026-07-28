# 运维 Runbook

面向生产日更：调度、健康门禁、备份恢复与回填验收。

> 历史路径 `docs/ops-runbook.md` 保留并重定向至本文。

---

## 组件一览

| 能力 | 脚本 | 作用 |
|------|------|------|
| 调度 | `scripts/daily_pipeline.sh` | 串行跑 6 个 schedule group + 健康检查 + 备份 |
| 调度 | `scripts/install_scheduler.sh` | 安装 macOS launchd（每天 16:05） |
| 调度 | `scripts/uninstall_scheduler.sh` | 卸载 launchd |
| 告警 | `scripts/health_notify.sh` | `audit --full` + `status --datasets` + macOS 通知 |
| 备份 | `scripts/backup_meta.sh` | manifest + state + quality 的 tar 轮换 |

脚本使用仓库 `.venv/bin/asl`，路径相对仓库根目录自解析。

---

## 安装调度

```bash
cd /path/to/ashare-lake
scripts/install_scheduler.sh
```

- 生成 `~/Library/LaunchAgents/com.asharelake.daily.plist`
- **每天本地 16:05** 触发（收盘后）
- 非交易日自动跳过（退出 0）
- **漏跑 / 周末补数**：`uv run asl run catchup`（门禁 core + breadth；水位已齐则
  `skipped_already_fresh`），或 `scripts/daily_pipeline.sh YYYY-MM-DD` /
  `ASL_TRADE_DATE=...`（全组定点）
- **海外 Mac**：保 `core`（+ 本地 derive breadth）即可；东财组留给
  国内机器 `catchup --all-groups` / 全组 pipeline。SOCKS 出口不够，见
  [troubleshooting](troubleshooting.md#云主机--socks-能开-ipinfo-但东财-empty-reply)。

```bash
launchctl list | grep asharelake
launchctl start com.asharelake.daily   # 手动触发
scripts/uninstall_scheduler.sh
```

**Linux cron**（建议跑在大陆出口）：

```cron
5 16 * * * /path/to/ashare-lake/scripts/daily_pipeline.sh
```

---

## 每日 Pipeline

```
core → capital → signals → fundamentals → macro_risk → research
  → health_notify.sh
  → backup_meta.sh
  → group summary（gate vs soft）
```

- 单组失败不中断后续组（尽量多采数据）
- 结尾摘要区分 **gate**（默认 `ASL_GATE_GROUPS=core`）与 **soft**（东财等）
- 默认 `ASL_SOFT_FAIL_OK=1`：gate OK 时 soft 失败 **warn-only、exit 0**（海外 Mac 预期东财滞后）；
  国内全组日更可设 `ASL_SOFT_FAIL_OK=0` 让 soft 失败仍 exit 1
- 东财超时/连接失败不重试（`[sources.eastmoney] timeout_sec`，默认 15s）
- 生产 `daily_pipeline.sh` 常设 `workers=1`（TDX 客户端与多进程兼容性）

组与 step 映射见 [配置 — 调度组](../getting-started/configuration.md#调度组)。

---

## 日志

目录：`{data.root}/logs/`

| 文件 | 内容 |
|------|------|
| `daily-YYYYMMDD.log` | 各组 asl 输出 |
| `health-YYYYMMDD.log` | audit / status 全文 |
| `launchd.out.log` / `launchd.err.log` | launchd 标准流 |

---

## 日常巡检命令

```bash
asl status --datasets          # 新鲜度；STALE 时退出 1
asl audit --full               # 湖级健康；UNHEALTHY 退出 1
asl catalog                    # 行数概览
```

---

## 失败处置

1. 查看 `daily-*.log` 定位失败组
2. 重跑单组：`asl run daily --group <name>`
3. 批级失败：`asl status` → `asl retry --run-id <id>`
4. 复核：`asl audit --full` + `asl status --datasets`

详见 [故障排查](troubleshooting.md)。

---

## 服务目标（SLO）

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
scripts/backup_meta.sh "" /Volumes/ext/asl-bak 30
```

**恢复**：

```bash
cd data/ashare-lake/meta
tar -xzf ../backups/meta-YYYYMMDD-HHMMSS.tar.gz
asl status    # 确认水位恢复
asl run daily --group core   # 增量续采
```

默认备份在湖内，磁盘级容灾请将 `ASL_BACKUP_DIR` 指到湖外。

---

## 环境变量

| 变量 | 默认 | 作用 |
|------|------|------|
| `ASL_CONFIG` | `configs/ashare-lake.toml` | 配置 |
| `ASL_LOG_DIR` | `{data.root}/logs` | 日志 |
| `ASL_GROUPS` | 全部 6 组 | 覆盖 pipeline 组列表 |
| `ASL_NOTIFY` | `1` | `0` 关闭通知 |
| `ASL_BACKUP_DIR` | 湖内 backups | 备份目录 |
| `ASL_BACKUP_RETENTION_DAYS` | 14 | 保留天数 |

---

## 数据湖目录（init 后）

```
{data.root}/
  staging/
  curated/
  derived/
  meta/manifest.db
  meta/quality/
  meta/source_snapshots/
  meta/on_demand/
  duckdb/ashare-lake.duckdb
```

---

## 分组 cron 示例

分组模式（`--group`）各组末尾会自动 compact→audit，数据写入 curated：

```cron
# 核心参考 + 行情 + 派生（周一至周五 16:05）
5 16 * * 1-5 cd /path/to/ashare-lake && asl run daily --group core --config configs/ashare-lake.toml

# 资金面（16:35）
35 16 * * 1-5 asl run daily --group capital --config configs/ashare-lake.toml

# 信号类（17:05）
5 17 * * 1-5 asl run daily --group signals --config configs/ashare-lake.toml
```

生产更推荐用 `scripts/daily_pipeline.sh`（见上文），它会串行跑完全部组并做健康检查与备份。

---

## Init 与资源

```bash
asl init --config configs/ashare-lake.toml
```

2016 起全量 init 大约 1.5–2.5 小时（TDX 分页 + Sina 复权；compact 内存尖峰约 2 GB）。
macOS 上必须 `[orchestrator].workers = 1`（TDX 客户端与 `ProcessPoolExecutor` 不兼容；
`asl config validate` 在 Darwin 上会拒绝 `workers>1`）。
单实例、收盘后运行。

---

## 回填完成验收

Init 或首次全量回填 compact + derive 成功且 `asl status` 为 success 后，在同一维护窗口内做下列检查，再挂 cron / 接下游。

### 前置

```bash
asl status --config configs/ashare-lake.toml          # success，failed batch = 0
asl audit  --config configs/ashare-lake.toml          # 无 mock_source / pk_duplicate error
ls data/ashare-lake/curated/daily_bars/       # 应有 trade_date=YYYY-MM-DD 分区
```

若配置里 `[adj_factors].adjust_types` 只有 `qfq` 而你要用后复权，先追加 `"hfq"` 并重跑
`asl derive adj_factors`。

### 幂等

```bash
.venv/bin/python scripts/accept_backfill.py snapshot \
  --config configs/ashare-lake.toml --out /tmp/curated-counts.json

asl run daily --config configs/ashare-lake.toml

.venv/bin/python scripts/accept_backfill.py check \
  --config configs/ashare-lake.toml --compare /tmp/curated-counts.json
```

核心数据集（`daily_bars`、`instruments`、`adj_factors` 等）行数应与重跑前一致。

### 口径抽查

```bash
.venv/bin/python scripts/accept_backfill.py check \
  --config configs/ashare-lake.toml \
  --symbol 600519.SH --start 2024-01-01 --end 2024-12-31
```

对照行情软件的未复权 close 与后复权 adj_close（除权日前后各抽一天）。

### 按年覆盖

```bash
.venv/bin/python scripts/accept_backfill.py check --config configs/ashare-lake.toml
# 看 === daily_bars by year ===
```

正常形态：2016→近年 symbols 缓增，每年 `rows ≈ symbols × ~240` 交易日，无单年腰斩。
若某年明显低于中位数 70%，对该年窗口做 `asl backfill daily_bars` 或 targeted retry。

### 消费层冒烟

```python
from ashare_lake.query import load

raw = load("daily_bars", start="2024-06-01", end="2024-06-30")
tradable = load(
    "daily_bars",
    start="2024-06-01",
    end="2024-06-30",
    adjust="hfq",
    universe="all_a",
)
assert tradable.height < raw.height
assert "adj_close" in tradable.columns
```

### 验收 checklist

| # | 项 | 通过标准 |
|---|-----|----------|
| 1 | 幂等 | 同窗口重跑后核心数据集 row count 不变 |
| 2 | 口径 | 标杆股 close/adj_close 与行情软件一致（人工） |
| 3 | 覆盖 | 按年行数无异常断崖；2016 起分区连续 |
| 4 | 消费 | `load(..., universe="all_a")` 剔除 ST/停牌；`adj_close` 可算 |
| 5 | 审计 | 最新 run audit 无 error；`source=mock` 行数 = 0 |

---

## 备源策略

1. 主源失败 → batch 退避重试（最多 3 次）
2. 仍失败 → 标记 batch failed；可选备源写入 `meta/source_snapshots`
3. `asl audit` 对比主源与 snapshot，由人决定是否切源
4. 不要静默用备源覆盖 curated canonical 行

---

## 相关文档

- [脚本说明](scripts.md)
- [故障排查](troubleshooting.md)
- [Schema 契约](../datasets/schema.md)
- [逐源限制](../datasets/sources.md)
