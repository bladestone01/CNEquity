# storage 模块

路径：`src/ashare_lake/storage/`

数据湖物理读写：目录初始化、staging/curated Parquet、compact 合并、instruments 特殊逻辑、水位、原子写、快照与清理。

---

## 文件一览

| 文件 | 职责 |
|------|------|
| `layout.py` | `init_data_layout()` — 建目录、manifest、DuckDB |
| `parquet.py` | `StagingWriter`, `CuratedWriter`, `compact_dataset()` |
| `instruments.py` | instruments 合并 compact，保留退市股 |
| `state.py` | `StateStore` — `meta/state/{dataset}.json` 水位 |
| `atomic.py` | 写临时文件 → rename |
| `source_snapshots.py` | `SnapshotStore` — failover 备源落地 |
| `staging_cleanup.py` | `clean_staging()` — `asl clean` |

---

## layout.py

`init_data_layout(cfg)` 创建：

```
staging/, curated/, derived/, raw/, meta/, duckdb/, meta/locks/
```

并初始化空 `manifest.db`、刷新 DuckDB 视图。

---

## parquet.py

### StagingWriter

路径：`staging/{dataset}/run_id={run_id}/part-{batch_id}.parquet`

- 写前 `validate_dataframe`
- 允许多 part 同 PK（compact 去重）

### compact_dataset(cfg, dataset, run_id)

1. 读取本 run staging parts
2. `compact_gate` 检查 batch 完整性
3. 按 `partition_col` 分组
4. PK dedupe：`sort("fetched_at").unique(subset=pk, keep="last")`
5. 与已有 curated 分区 merge 后再 dedupe
6. `atomic_write_parquet` 写 `part-merged.parquet`
7. 成功则 `StateStore.advance_watermark(dataset, ...)`

### instruments 例外

调用 `instruments.py` 的 `compact_instruments()`：按 symbol 外连接合并，**不删除**仅存在于旧 curated 的退市 symbol。

---

## state.py

`meta/state/{dataset}.json` 示例：

```json
{
  "last_success_date": "2026-07-11",
  "updated_at": "2026-07-12T08:00:00Z"
}
```

- `watermark=False` 的数据集（如 instruments、financial_statement_items）不写水位
- 增量窗口：`steps/common.incremental_window()` 读取

---

## atomic.py

防止 compact 中途崩溃留下半截 Parquet：先写 `.tmp` 再 `os.replace`。

---

## source_snapshots.py

路径：`meta/source_snapshots/{dataset}/source={src}/data_version={ver}/...`

- 主源 batch 失败时由 `quality/failover.py` 写入
- **不**进入 curated
- `audit` 的 `source_diff.py` 读取比对

---

## staging_cleanup.py

`asl clean` 逻辑：

| 条件 | 行为 |
|------|------|
| run 已终态（success / warning / failed）且无 incomplete batch，并已记录成功的 compact | 删除其 staging（compact 后 staging 已冗余） |
| 无 manifest 记录的 orphan staging | 超过 retention 天删除 |
| incomplete 或尚未 compact 的 run | 默认保留（可 `asl retry`）；`--force` 删除并 demote 成功 batch |

---

## 相关文档

- [数据湖布局](../architecture/lake-layout.md)
- [ADR-0003](../adr/0003-canonical-curated-with-source-snapshots.md)
