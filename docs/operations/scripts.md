# 脚本说明

路径：`scripts/`

运维与一次性工具脚本。生产日更以 `daily_pipeline.sh` 为主路径。

---

## 生产脚本

### daily_pipeline.sh

**用途**：交易日串行执行全部 schedule groups，末尾健康检查与备份。

**流程**：

```
for group in core capital signals fundamentals macro_risk research; do
  sde run daily --group $group
done
health_notify.sh
backup_meta.sh
```

**环境变量**：`SDE_CONFIG`, `SDE_LOG_DIR`, `SDE_GROUPS`,
`SDE_GATE_GROUPS`（默认 `core`，失败标为 gate；其余组标 soft）、`SDE_TRADE_DATE`。

结束时打印分组摘要（`group: OK|FAILED [gate|soft]`），便于区分「门禁挂了」与「东财挂了」。

---

### install_scheduler.sh / uninstall_scheduler.sh

从 `scripts/launchd/com.stockdataengine.daily.plist.template` 生成用户 launchd plist，加载 `daily_pipeline.sh`。

---

### health_notify.sh

```bash
sde audit --full
sde status --datasets
```

失败时 macOS `osascript` 通知，退出码非零。

---

### backup_meta.sh

打包 `meta/manifest.db`、`meta/state/`、`meta/quality/` 为 `meta-YYYYMMDD-HHMMSS.tar.gz`，按保留天数清理旧包。

参数：`backup_meta.sh [config_path] [backup_dir] [retention_days]`

---

## Init 与验收

### run_init_2016.py

辅助全量历史 init（2016 起）的包装脚本，封装推荐参数与环境检查。

### retry_init_finalize.py

init 完成后若 finalize（compact/derive/audit）失败，单独重试 finalize 步骤。

### accept_backfill.py

回填验收工具：

```bash
python scripts/accept_backfill.py snapshot --out /tmp/counts.json
python scripts/accept_backfill.py check --compare /tmp/counts.json
```

检查幂等性与 curated 行数稳定性。

---

## 测试与冒烟

### smoke_daily_e2e.py

端到端冒烟：mock 或轻量配置下跑 miniature daily 路径，CI/本地回归用。

---

## launchd 模板

`scripts/launchd/com.stockdataengine.daily.plist.template`

- `ProgramArguments` 指向 `daily_pipeline.sh`
- `StartCalendarInterval`：Hour=16, Minute=5
- 标准输出/错误重定向到 `{data.root}/logs/launchd.*.log`

---

## 相关文档

- [运维 Runbook](runbook.md)
- [快速开始](../getting-started/quickstart.md)
