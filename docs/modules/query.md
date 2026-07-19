# query 模块

路径：`src/ashare_lake/query/`

消费层：Python 读取 API、DuckDB 视图、Universe 过滤、Parquet 分区扫描、On-Demand 服务。

---

## 文件一览

| 文件 | 职责 |
|------|------|
| `reader.py` | `load()`, `scan()`, `list_datasets()`, `dataset_schema()` |
| `views.py` | DuckDB 视图生成；`daily_bars_*` 复权视图 |
| `universe.py` | `apply_universe_filter()` — `all_a` |
| `parquet_scan.py` | Hive 分区裁剪、lazy scan |
| `on_demand.py` | `OnDemandService` — 按需抓取 + JSON 缓存 |
| `__init__.py` | 导出 `load`, `scan`, `list_datasets` |

---

## reader.py

### load(dataset, *, start, end, adjust, universe, as_of, items, symbols, strict_adj, config, data_root)

主 API。流程：

1. `resolve_config()` 解析配置
2. `_read_dataset()` — parquet_scan + schema 校验
3. 可选：`_apply_date_range`
4. 可选：`adjust` → join `adj_factors`，计算 adj_* 列
5. 可选：`universe="all_a"`
6. 可选：PIT `as_of` 过滤
7. 返回 `pl.DataFrame`

### scan(...)

同上但返回 `LazyFrame`，适合大窗口。

### list_datasets(config=...)

DataFrame 列：`dataset`, `has_data`, `files`, `rows`, `watermark`, `coverage_start/end`, `watermarked`, `freshness`（若计算）

---

## 复权实现要点

- 存储类型：`STORED_ADJUST_TYPE = "hfq"`（来自 derive 模块）
- join key：`(symbol, trade_date)`
- `adj_is_exact`：因子齐全为 True
- qfq：`hfq_factor / hfq_factor_anchor`（anchor = 窗口内最新交易日）

---

## views.py

`ensure_duckdb_views(cfg)`：

- 扫描 `curated/`、`derived/` 生成 `CREATE OR REPLACE VIEW`
- 特殊视图 `daily_bars_hfq`, `daily_bars_qfq`, `daily_bars_adj`
- 视图定义与 `load(adjust=...)` 语义对齐

---

## universe.py

`all_a` 过滤步骤：

1. join instruments（上市/退市）
2. 排除 CDR（689）与场内 ETF/LOF（SH `51/52/56/58`，SZ `15/16`）
3. left join trading_status；有行时剔除 ST/suspended

ETF 仍可留在 instruments / daily_bars（UI/报价），但不进研究宇宙。

---

## parquet_scan.py

- `scan_parquet_root(root, partition_col, start, end, symbols)`
- 根据分区目录名裁剪日期范围
- `collect_parquet_root` 物化

---

## on_demand.py

```python
svc = OnDemandService(cfg)
data = svc.fetch("stock_news", "600519.SH")
```

缓存：`meta/on_demand/{dataset}/{symbol}.json`

---

## 相关文档

- [查询指南](../datasets/query-guide.md)
- [Python API 参考](../reference/python-api.md)
