# domain 模块

路径：`src/stock_data_engine/domain/`

**数据契约层**：schema 类型、主键、数据集元数据、符号规则、跨进程限速、情绪打分工具。不含 I/O 与编排。

---

## 文件一览

| 文件 | 职责 |
|------|------|
| `schemas.py` | Polars schema、`PRIMARY_KEYS`、`validate_dataframe()`、`with_provenance()` |
| `datasets.py` | `DatasetSpec` 注册表 `DATASETS` |
| `symbols.py` | `parse_symbol()`, `is_all_a_symbol()`, CDR 排除 |
| `rate_limit.py` | 文件锁 + JSON 时间戳的跨进程 `RateLimiter` |
| `sentiment.py` | 公告关键词 + 可选 SnowNLP 打分 |

---

## schemas.py

### 核心常量

- `DATASET_SCHEMAS: dict[str, dict[str, pl.DataType]]` — 每数据集列类型
- `PRIMARY_KEYS: dict[str, list[str]]` — 主键列
- `MOCK_SOURCE = "mock"` — 测试源标识

### 溯源列

每个 curated schema 末尾包含：

```python
"source": pl.Utf8,
"data_version": pl.Utf8,
"fetched_at": pl.Datetime("us", "UTC"),
```

### validate_dataframe(df, dataset)

写 staging/curated 前调用：

- 列齐全且类型匹配
- 不允许未知列（strict）
- PK 非空

### with_provenance(df, source, data_version)

为 adapter 输出批量添加溯源列。

---

## datasets.py

### DatasetSpec 字段

| 字段 | 含义 |
|------|------|
| `name` | 数据集名 |
| `layer` | `curated` / `derived` |
| `partition_col` | Hive 分区列；`None` = merge 文件 |
| `date_col` | 查询日期列；默认等于 `partition_col` |
| `fetch_semantics` | `by_date` / `snapshot` |
| `watermark` | 是否维护 `meta/state` 水位 |
| `pit` | 是否 PIT 数据集 |
| `backfill_source` | snapshot 数据集的历史回填源名 |
| `max_staleness_days` | `status --datasets` 容忍滞后天数 |

### 辅助函数

```python
get_dataset(name) -> DatasetSpec
curated_dataset_names() -> frozenset[str]
derived_dataset_names() -> frozenset[str]
pit_dataset_names() -> frozenset[str]
fetch_semantics(dataset) -> Literal["by_date", "snapshot"]
is_stale(dataset, mark, anchor) -> bool
```

**新增数据集必须**：在此添加 `DatasetSpec` + 在 `schemas.py` 添加 schema/PK。`tests/unit/test_dataset_registry.py` 强制同步。

---

## symbols.py

- 格式：`600519.SH`、`000001.SZ`、`920001.BJ`
- `is_all_a_symbol()`：沪深 A 股，排除 CDR（689 段）
- `parse_symbol()` → `(code, exchange)`

Universe 过滤在 `query/universe.py` 使用本模块规则。

---

## rate_limit.py

跨进程限速：锁文件 + 上次请求时间 JSON。供 `adapters/throttle.py` 与 HTTP adapter 使用，防止多 worker 打爆源站。

---

## sentiment.py

`research` step 使用：

- 公告标题/正文关键词情绪
- `use_snownlp=true` 时调用 SnowNLP（可选依赖）

---

## 相关文档

- [数据集目录](../datasets/catalog.md)
- [新增数据集](../development/adding-dataset.md)
