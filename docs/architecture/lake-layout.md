# 数据湖布局

数据湖根目录：`{data.root}`（配置项 `[data].root`）。

---

## 顶层结构

```
{data_root}/
├── staging/          # 本次 run 原始落地（compact 后可清理）
├── curated/          # 下游只读的 canonical 数据
├── derived/          # 可重算的派生数据
├── raw/              # 可选：原始 HTTP 响应留存
├── meta/             # 编排元数据、水位、质量、缓存
├── duckdb/           # DuckDB 视图数据库
├── logs/             # 运维脚本日志（gitignored）
└── backups/          # 元数据备份（可选）
```

---

## staging/

```
staging/{dataset}/run_id={run_id}/part-{batch_id}.parquet
```

- 每次 run 独立 `run_id`（UUID）
- worker step 每 batch 一个 part 文件
- 非 worker step 通常单 part
- **不保证** PK 唯一；去重在 compact 阶段完成

清理：`asl clean`（成功 compact 的 run；`--force` 可清失败 run，retry 将全量重抓）

---

## curated/

```
curated/{dataset}/{partition_col}={value}/part-merged.parquet
```

**核心契约**：

- 每个主键（PK）**恰好一行** canonical 记录
- 每行必含溯源列：`source`、`data_version`、`fetched_at`（UTC）
- 多源差异**不**在 curated 内共存；备源见 `meta/source_snapshots/`

**特殊：instruments**

- 无 Hive 分区，单文件 merge 语义
- compact 时**合并**而非覆盖，保留已退市 symbol（防幸存者偏差）

**分区键**：见 [数据集目录](../datasets/catalog.md)。常见：

- `trade_date` — 日线类
- `ex_date` — 除权除息
- `report_period` — 财报
- `as_of_date` — 快照类成员关系
- `announce_date` — 公告（PIT）

---

## derived/

```
derived/adj_factors/trade_date=YYYY-MM-DD/part-*.parquet
```

当前派生数据集：

| 数据集 | 来源 | 说明 |
|--------|------|------|
| `adj_factors` | Sina hfq | 查询期与 daily_bars 组合复权 |
| `market_breadth` | daily_bars 计算 | 也可写在 curated（当前在 curated 注册） |
| `sentiment_scores` | 公告 + 新闻 | 写在 curated |

`adj_factors` 另有缓存：`meta/adj_factors_cache/{symbol}.parquet`

---

## meta/

```
meta/
├── manifest.db                    # SQLite：ingestion_runs, ingestion_batches
├── state/
│   └── {dataset}.json             # 增量水位（last_success_date 等）
├── source_snapshots/
│   └── {dataset}/source={src}/data_version={ver}/...
├── quality/
│   ├── findings/{run_id}.json
│   └── source_diffs/{run_id}.json
├── adj_factors_cache/
├── on_demand/
│   └── {dataset}/{symbol}.json
└── locks/                         # run_lock 文件锁
```

### manifest.db

表：

- `ingestion_runs` — job_name, status, started_at, finished_at
- `ingestion_batches` — dataset, batch_id, status, symbol_range, error_message

Batch 状态机：`pending` → `running` → `success` | `failed` | `stale`

### state/{dataset}.json

compact 成功后更新。用于：

- 增量抓取窗口（`incremental_window`）
- 下游缓存失效键
- `asl status --datasets` 新鲜度判断

---

## duckdb/

```
duckdb/ashare-lake.duckdb
```

- 启动时 / `asl query` 前由 `query/views.py` 确保视图存在
- 每个 curated/derived 数据集对应视图
- 额外视图：`daily_bars_hfq`、`daily_bars_qfq`、`daily_bars_adj`（带复权列）

路径配置：`[duckdb].path`，支持 `{data.root}` 占位符。

---

## 读写权限约定

| 路径 | 写入方 | 读取方 |
|------|--------|--------|
| staging | steps（采集） | compact |
| curated | compact | load() / DuckDB / 外部 Polars |
| derived | derive steps | load() |
| meta/* | orchestrator, quality, derive | CLI, audit, load（水位） |

**下游禁止写入 curated/derived**，保证可重放与审计。

---

## 相关文档

- [ADR-0002：Parquet 湖](../adr/0002-parquet-lake-over-database.md)
- [ADR-0003：Canonical + Snapshots](../adr/0003-canonical-curated-with-source-snapshots.md)
- [storage 模块](../modules/storage.md)
