# cli 模块

路径：`src/stock_data_engine/cli/main.py`

Click 命令组 `sde` 的唯一实现。入口：`pyproject.toml` `[project.scripts]` 与 `__main__.py`。

---

## 全局行为

- 默认配置：`configs/stockdata.toml`；不存在时提示复制 example
- 启动时 `import stock_data_engine.steps` 注册全部 step
- 失败命令多数 `raise SystemExit(1)`，供 launchd/cron 检测

### 退出码约定

| 场景 | 退出码 |
|------|--------|
| 成功 | 0 |
| `skipped_non_trading_day` | 0 |
| run/audit/init 失败 | 1 |
| `status --datasets` 有 STALE | 1 |
| `audit --full` UNHEALTHY | 1 |

---

## 命令结构

```
sde
├── init
├── config validate
├── run daily [--group] [--backfill]
├── backfill <dataset>
├── compact [--run-id]
├── derive [adj_factors|trading_status]
├── audit [--run-id] [--full]
├── status [--datasets]
├── retry --run-id
├── clean [--dry-run] [--orphan-retention-days] [--force]
├── catalog
├── query [--sql] [--dataset --symbol]
└── servers test
```

完整参数见 [CLI 参考](../reference/cli.md)。

---

## 实现要点

| 命令 | 核心调用 |
|------|----------|
| init | `init_data_layout` + `JobEngine.run_init_phases` |
| run daily | `JobEngine.run_job` |
| backfill | `run_job("backfill")` + `step_compact` |
| compact | `step_compact` |
| derive | `compute_adj_factors` / `derive_suspension_history` |
| audit | `run_audit` / `lake_health` |
| status | `Manifest.run_summary` / `list_datasets` |
| retry | `resume_init` / `run_job("retry")` |
| clean | `clean_staging` |
| catalog | 扫描 curated parquet 元数据 |
| query | DuckDB 或 `OnDemandService` |
| servers test | `tdx_protocol.client._quotes_client` |

---

## resolve_config_path

统一配置路径解析与友好错误信息。

---

## 相关文档

- [CLI 参考](../reference/cli.md)
- [快速开始](../getting-started/quickstart.md)
