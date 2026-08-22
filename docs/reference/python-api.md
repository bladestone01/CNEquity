# Python API 参考

模块：`cnequity.query`

```python
from cnequity.query import load, scan, list_datasets, dataset_schema
```

---

## load()

```python
def load(
    dataset: str,
    *,
    start: str | date | None = None,
    end: str | date | None = None,
    adjust: Literal["qfq", "hfq"] | None = None,
    universe: Literal["all_a", "all_a_sh_sz"] | None = None,
    as_of: str | date | None = None,
    items: list[str] | None = None,
    symbols: list[str] | None = None,
    strict_adj: bool = False,
    strict_universe: bool = False,
    all_vintages: bool = False,
    config: Config | None = None,
    data_root: str | Path | None = None,
) -> pl.DataFrame
```

### 参数

| 参数 | 说明 |
|------|------|
| `dataset` | 注册数据集名 |
| `start`, `end` | 含边界日期窗口（数据集主日期列） |
| `adjust` | `hfq` / `qfq`；适用于 `daily_bars`、`minute_bars`、`minute_bars_5m` 等价量数据集 |
| `universe` | `"all_a"` 沪深北全 A；`"all_a_sh_sz"` 明确限定沪深子集并排除北交所 |
| `as_of` | PIT 截止日：过滤 `announce_date` 与 `fetched_at.date()` 均不晚于截止日，并对同一科目取当时生效的那一版 |
| `items` | 财报科目 code 列表 |
| `symbols` | symbol 白名单 |
| `strict_adj` | True 时缺复权因子抛 `ReaderError` |
| `strict_universe` | True 时支持的 universe 缺少 instruments、逐日 trading_status 覆盖或版本化历史 ST 证据收据会抛错；适合研究读取 |
| `all_vintages` | True 时返回 `as_of` 前的**全部**版本（研究财报修订用）；截面选股勿开，会重复计同一事实 |
| `config` / `data_root` | 湖位置；默认读 `configs/cnequity.toml` |

### 返回

- 未复权数据集：原始列
- `adjust` 非空：附加 `adj_open`, `adj_high`, `adj_low`, `adj_close`, `adj_is_exact`

### 异常

`ReaderError`（`ValueError` 子类）：未知数据集、无数据、strict_adj 失败等。

---

## scan()

返回原始 `pl.LazyFrame`，适合大窗口的自定义 lazy 管道。它只做日期分区和
symbol 过滤，不执行 `load()` 的复权、universe、PIT 或严格覆盖语义；需要这些
语义时应使用 `load()`。当前参数如下：

```python
def scan(
    dataset: str,
    *,
    start: str | date | None = None,
    end: str | date | None = None,
    symbols: list[str] | None = None,
    config: Config | None = None,
    data_root: str | Path | None = None,
) -> pl.LazyFrame
```

```python
lf = scan("daily_bars", start="2020-01-01", symbols=["600519.SH"])
df = lf.filter(pl.col("close") > 0).collect()
```

---

## list_datasets()

```python
def list_datasets(
    *,
    config: Config | None = None,
    data_root: str | Path | None = None,
) -> pl.DataFrame
```

列：`dataset`, `layer`, `date_col`, `fetch_semantics`, `history_mode`, `backfill_source`, `pit`, `has_data`, `coverage_start`, `coverage_end`, `watermarked`, `watermark`

`history_mode` ∈ `by_date` / `snapshot_with_backfill` / `snapshot_only`；与 `coverage_*` 一起构成可用起点合同。

## historical_universe_validity()

历史研究门禁位于 `cnequity.quality.historical_validity`，默认验证沪深北全 A：

```python
from cnequity.quality.historical_validity import historical_universe_validity
from datetime import date

report = historical_universe_validity(
    cfg,
    start=date(2020, 1, 1),
    end=date(2024, 12, 31),
    universe="all_a_sh_sz",
)
assert report["universe_ready"]
```

`universe="all_a_sh_sz"` 会让日线区间、历史 ST 证据和退市覆盖都只按沪深
子集核验；它不会把全 A (`all_a`) 的 BJ 历史证据缺口隐藏掉。`universe_ready`
为真才表示该明确口径可以进入历史研究。

---

## dataset_schema()

```python
def dataset_schema(dataset: str) -> dict[str, pl.DataType]
```

返回 `domain/schemas.py` 中注册的 Polars 类型映射。

---

## 配置解析

```python
from cnequity.query.reader import resolve_config

cfg = resolve_config(config=my_cfg)
cfg = resolve_config(data_root="/path/to/lake")
```

优先级：`config` > `data_root` > 默认 toml 路径。

---

## 示例

### 后复权全市场

```python
bars = load(
    "daily_bars",
    start="2024-01-01",
    end="2024-12-31",
    adjust="hfq",
    universe="all_a",
    strict_adj=True,
)
```

### PIT 财报

```python
roe = load(
    "financial_statement_items",
    items=["roe"],
    as_of="2024-04-30",
)
```

### 指数行情

```python
idx = load("index_bars", start="2024-01-01", symbols=["000300.SH"])
```

### 显式 data_root（无需 toml）

```python
bars = load("daily_bars", start="2024-06-01", data_root="/data/cnequity")
```

---

## DuckDB 等价

视图由 `query/views.py` 维护。SQL 用户可用 `cne query` 或直连 duckdb 文件，语义应与 `load()` 对齐（复权视图见 `daily_bars_adj`）。

---

## 相关文档

- [查询指南](../datasets/query-guide.md)
- [query 模块](../modules/query.md)
