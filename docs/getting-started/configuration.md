# 配置参考

配置文件格式：TOML。模板：`configs/stockdata.example.toml`。加载与校验：`stock_data_engine.config.loader`。

```bash
sde config validate --config configs/stockdata.toml
```

---

## `[data]`

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `root` | string | `./data/stock-data-engine` | 数据湖根目录；**生产建议绝对路径** |

派生路径（代码内自动计算，无需配置）：

- `{root}/staging` — 本次 run 原始落地
- `{root}/curated` — canonical 数据集
- `{root}/derived` — 派生数据集（如 adj_factors）
- `{root}/meta` — manifest、水位、质量 findings
- `{root}/duckdb/stockdata.duckdb` — DuckDB 视图库

---

## `[orchestrator]`

| 键 | 默认 | 说明 |
|----|------|------|
| `workers` | 8 | `daily_bars` 多进程 worker 数 |
| `batch_size` | 100 | 每 batch 股票数量 |
| `max_retries` | 3 | batch 级重试次数 |
| `retry_backoff_seconds` | 5 | 重试退避 |
| `batch_stale_seconds` | 3600 | running batch 无心跳超时 → stale → failed；compact 门禁会跳过未完成数据集 |

---

## `[tdx_protocol]`

| 键 | 默认 | 说明 |
|----|------|------|
| `enabled` | true | 禁用后 TDX 相关 step 失败 |
| `min_interval_ms` | 100 | 跨进程限速间隔（建议 ≥100，防多 job 打爆） |
| `servers` | `"auto"` | `"auto"` 或 `"host:port"` 固定单服 |
| `connect_timeout_sec` | 10 | 连接超时 |
| `allow_mock` | false | **仅测试**：源不可用时返回 `source="mock"` 数据；生产必须 false |

### `[tdx_protocol.hosts]`

| 键 | 说明 |
|----|------|
| `standard` | `servers="auto"` 时优先并行探测的 A 股标准行情主机列表；为空则用 mootdx 内置列表 |

---

## `[sources.<name>]`

支持的 name：`eastmoney`、`cninfo`、`akshare`、`sina`、`baostock`。

| 键 | 说明 |
|----|------|
| `enabled` | 是否启用该源 |
| `min_interval_seconds` | 跨进程文件锁限速（见 `domain/rate_limit.py`） |
| `proxy`（eastmoney） | 可选 HTTP(S) 代理 URL；用于海外访问 push2his。未设时仍可用环境变量 `HTTPS_PROXY` |
| `batch_size` / `batch_rest_seconds`（baostock） | 全市场回填批次冷却，防 IP 黑名单 |

推荐默认（时间宁可慢，勿被封）：

| source | `min_interval_seconds` | 备注 |
|--------|------------------------|------|
| eastmoney | 1.0 | 日更主源；裸 `EastMoneyClient()` 也默认 1.0s 进程内节流 |
| cninfo | 1.0 | 公告/监管分页 POST |
| akshare | 1.0 | 底层常打东财，勿低于 EM |
| sina | 0.3 | 复权因子；经 `adj_factors` 的 `wait_source` |
| baostock | 1.0 + batch 50/45s | 历史市值/ST；禁止多进程并行扫 |

---

## `[adj_factors]`

| 键 | 默认 | 说明 |
|----|------|------|
| `source` | `"sina"` | 复权因子来源 |
| `adjust_types` | `["hfq"]` | 仅存后复权因子（ADR-0004）；qfq 查询期派生 |

---

## `[sentiment]`

| 键 | 默认 | 说明 |
|----|------|------|
| `use_snownlp` | false | on-demand `stock_news` 可选 SnowNLP（需 `[nlp]`）；日更 batch 用关键词 |
| `news_symbol_limit` | 50 | HTTP `stock_news` 回退抓取 symbol 上限（主通道为 curated `news_headlines`） |

---

## `[failover]`

多源快照与 diff，**永不自动切换 canonical**（ADR-0003）。

| 键 | 说明 |
|----|------|
| `enabled` | 总开关 |

### `[[failover.datasets]]`

| 键 | 说明 |
|----|------|
| `name` | 数据集名 |
| `primary` | 主源 adapter 名 |
| `backup` | 备源（主源 batch 失败时写 snapshot） |
| `compare_fields` | audit diff 比对字段 |
| `price_tolerance_bps` | 价格容差（基点） |

默认配置：`daily_bars`（TDX 主 / EM 备）、`corporate_actions`（EM 主 / TDX 备）。

---

## `[universe]`

| 键 | 默认 | 说明 |
|----|------|------|
| `default` | `"all_a"` | `load(..., universe=)` 默认 universe 类型 |

---

## `[job.daily.waves]`

Wave DAG：每个 wave 含 `name`、`parallel`（wave 内 step 是否并行）、`steps`（step 名列表）。

默认四波：

1. `reference` — instruments, trading_calendar, trading_status（并行）
2. `corp_actions_to_bars` — corporate_actions → daily_bars（串行）
3. `parallel_core` — index_bars
4. `finalize` — compact, derive_adj_factors, audit

`validate_config` 要求至少一个 wave，且所有 step 名必须在 `STEP_REGISTRY` 中。

---

## 调度组

`[job.daily.groups.<name>]`：`at`（文档/调度参考时间）、`steps`（含末尾 `compact`）。

| 组名 | 典型时间 | 内容摘要 |
|------|----------|----------|
| `core` | 16:00 | L0 + L1 核心 + derive_adj_factors |
| `capital` | 16:30 | 资金面 + 估值 + 板块 + 公告索引 |
| `signals` | 17:00 | 龙虎榜、大宗交易 |
| `fundamentals` | 17:30 | 财报、指数成分、行业 |
| `macro_risk` | 18:00 | 宏观、市场宽度、解禁、监管 |
| `research` | 18:30 | 机构持仓、一致预期、情绪 |

`sde run daily --group <name>` 只跑该组 steps。

---

## `[job.init.phases]`

| 键 | 说明 |
|----|------|
| `names` | init 阶段顺序列表 |

默认：

```toml
names = [
  "phase1_reference",
  "phase2a_corporate_actions",
  "phase2c_daily_bars_backfill",
  "phase3_index_and_status",
  "phase4_finalize",
]
```

阶段 → step 映射见 `orchestrator/init_phases.py`。

---

## `[on_demand]`

| 键 | 说明 |
|----|------|
| `enabled` | OnDemandService 开关 |
| `datasets` | 按需抓取的数据集名列表 |

缓存路径：`meta/on_demand/{dataset}/{symbol}.json`。通过 `sde query --dataset X --symbol Y` 访问。

---

## `[duckdb]`

| 键 | 默认 | 说明 |
|----|------|------|
| `path` | `{data.root}/duckdb/stockdata.duckdb` | 支持 `{data.root}` 占位符 |
| `memory_limit` | `2GB` | DuckDB 内存上限 |
| `threads` | 4 | 查询线程数 |

---

## 环境变量（脚本层）

运维脚本读取，非 TOML 内置：

| 变量 | 默认 | 作用 |
|------|------|------|
| `SDE_CONFIG` | `configs/stockdata.toml` | 配置路径 |
| `SDE_LOG_DIR` | `{data.root}/logs` | 日志目录 |
| `SDE_GROUPS` | 全部 6 组 | 覆盖 pipeline 要跑的组 |
| `SDE_NOTIFY` | `1` | `0` 关闭 macOS 通知 |
| `SDE_BACKUP_DIR` | 湖内 backups | 元数据备份目录 |
| `SDE_BACKUP_RETENTION_DAYS` | 14 | 备份保留天数 |

---

## 配置与代码关系

```
stockdata.toml
    → load_config() → Config dataclass
    → validate_config() → 引用 step/group 合法性
    → JobEngine(cfg) / load(..., config=cfg)
```

`Config` 还提供：`staging_root`、`curated_root`、`derived_root`、`meta_root`、`manifest_path`、`rate_limit(source)`。
