# config 模块

路径：`src/ashare_lake/config/`

负责将 `ashare-lake.toml` 解析为类型化的 `Config` 对象，并在启动前校验引用完整性。

---

## 文件

| 文件 | 职责 |
|------|------|
| `loader.py` | `load_config()`, `validate_config()`, `Config` dataclass |
| `bootstrap.py` | `asl config init`：从包内模板写出用户 toml |
| `templates/ashare-lake.example.toml` | 随包装的示例配置（与仓库 `configs/` 副本同步） |
| `__init__.py` | 导出 `Config`, `load_config`, `validate_config`, `write_user_config` |

---

## Config  dataclass

主要字段：

| 字段 | 来源 TOML 段 |
|------|--------------|
| `data_root` | `[data].root` |
| `workers`, `batch_size`, … | `[orchestrator]` |
| `tdx_*` | `[tdx_protocol]` |
| `sources` | `[sources.*]` |
| `adj_factors`, `sentiment` | 同名段 |
| `failover` | `[failover]` |
| `daily_waves`, `schedule_groups`, `init_phases` | `[job.*]` |
| `on_demand`, `duckdb` | 同名段 |
| `universe_default` | `[universe].default` |

### 派生路径（property）

```python
cfg.staging_root    # data_root / "staging"
cfg.curated_root    # data_root / "curated"
cfg.derived_root    # data_root / "derived"
cfg.meta_root       # data_root / "meta"
cfg.manifest_path   # meta_root / "manifest.db"
```

### rate_limit(source)

懒加载 `SourceRateLimiters`（`adapters/throttle.py`），按 `[sources.<name>].min_interval_seconds` 与 TDX `min_interval_ms` 构造跨进程限速器。

---

## load_config(path)

1. `tomllib` 解析 TOML
2. 展开 `[duckdb].path` 中的 `{data.root}`
3. 构建 `Config`；保留 `config_path` 供日志

---

## validate_config(cfg) → list[str]

校验项：

- `workers >= 1`, `batch_size >= 1`
- TDX `servers` 格式（`auto` 或 `host:port`）
- `[job.daily.waves]` 非空
- 每个 wave/group/init phase 引用的 step 名 ∈ `STEP_REGISTRY`
- failover 数据集名合法

返回错误字符串列表；空列表表示通过。CLI：`asl config validate`。

---

## 使用示例

```python
from ashare_lake.config import load_config, validate_config

cfg = load_config("configs/ashare-lake.toml")
errors = validate_config(cfg)
```

`JobEngine(cfg)`、`load(..., config=cfg)`、`init_data_layout(cfg)` 均接受 `Config`。

---

## 相关文档

- [配置参考](../getting-started/configuration.md)
