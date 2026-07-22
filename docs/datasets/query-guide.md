# 查询指南

下游读数的推荐路径是 `ashare_lake.query.load()`。本文说明复权、Universe、PIT 与常见陷阱。

API 签名见 [Python API 参考](../reference/python-api.md)。

---

## 基本用法

```python
from ashare_lake.query import load, scan, list_datasets

# 物化 DataFrame
df = load("daily_bars", start="2024-01-01", end="2024-12-31")

# Lazy scan（大窗口）
lf = scan("daily_bars", start="2020-01-01", end="2024-12-31")

# 湖内数据集概览
meta = list_datasets()  # 或 list_datasets(config=cfg)
```

配置解析顺序：`config=` → `data_root=` → `configs/ashare-lake.toml`

---

## 复权（adjust）

仅适用于含价量列的数据集（主要是 `daily_bars`）。

| 参数 | 行为 |
|------|------|
| `adjust=None` | 原始未复权 OHLC |
| `adjust="hfq"` | 用后复权因子乘价格；增列 `adj_open`…`adj_close`、`adj_is_exact` |
| `adjust="qfq"` | 查询窗口内按最新 bar anchor 将 hfq 因子归一化（ADR-0004） |

```python
bars = load(
    "daily_bars",
    start="2020-01-01",
    end="2024-12-31",
    adjust="hfq",
)
```

### strict_adj

```python
bars = load("daily_bars", start="2024-01-01", adjust="hfq", strict_adj=True)
```

- `True`：缺因子行抛出 `ReaderError`，不填充 1.0
- `False`（默认）：缺因子时 `adj_is_exact=False`，价格按 factor=1.0 降级

**研究建议**：量化回测用 `hfq` + `strict_adj=True`；qfq 窗口 anchor 会漂移，不适合长期研究复现。

### 存储约定

- 湖内 `daily_bars` 存**未复权**价
- `adj_factors` 在 `derived/`，仅存 `hfq`（`adjust_type="hfq"`）
- DuckDB 视图 `daily_bars_adj` / `daily_bars_hfq` / `daily_bars_qfq` 与上述语义一致

---

## Universe 过滤

```python
bars = load("daily_bars", start="2024-01-01", universe="all_a")
```

`all_a` 规则（`query/universe.py`）：

1. **instruments**：`list_date <= trade_date`，且未退市或 `delist_date > trade_date`
2. 排除 CDR（689xxx.SH）
3. **trading_status**（仅有数据的日期）：剔除 ST/*ST 与停牌

### 历史 ST 限制

日更只抓当天 `trading_status`。2016 → 覆盖起点之间**无** ST 行，该区间 `all_a` **不会**剔除历史 ST。

审计项 `trading_status_coverage_start` 会报告覆盖起点。长期回测需知此偏差或运行 ST 历史回填（baostock + `asl derive trading_status`）。

### 交易所覆盖

`all_a` 含沪深北三所。但**北交所曾长期为空**——TDX 协议不提供北交所
（`mootdx` 直接报「市场代码错误, 目前只支持沪深市场」），而 `PREFIX_WHITELIST`
认 `92` 前缀，于是 `all_a` 名义三所、实际两所，任何"全 A 股"回测跑的都是沪深。

现在 BJ 行情走 Sina（`domain/symbols.py::split_by_quote_source` 分流），
instruments 每次日更从代码空间扫描的「在市但缺失」桶补齐。注意两点：

- BJ 行的 `source` 是 `sina`，且 **`amount` 为 null**（Sina 不给成交额），
  换手额类因子对北交所会缺失
- 新上市的北交所票要等下一次 `asl delisted discover` 扫到才会进 instruments，
  不是当天自动出现

---

## PIT（Point-in-Time）

PIT 数据集：`financial_statement_items`、`announcement_index`。

```python
items = load(
    "financial_statement_items",
    items=["roe", "net_profit"],
    as_of="2024-04-30",
)
```

- 过滤 `announce_date <= as_of`
- 同一 `(symbol, report_period, item)` 取 `announce_date` 最新一行
- 禁止用 `end=` 代替 `as_of` 做财报对齐

`announce_date` 在主键里，所以财报修订是**新增一版**而不是覆盖原值：同一科目可以同时存在
首发值和修订值。默认只返回 `as_of` 当时生效的那一版；要看修订本身（修订幅度和方向本身
就是信号）加 `all_vintages=True`：

```python
# 000001.SZ 2024Q1 营收被改过几次、每次改了多少
load(
    "financial_statement_items",
    symbols=["000001.SZ"], items=["revenue"],
    as_of="2026-07-21", all_vintages=True,
).select("report_period", "announce_date", "item_value")
```

注意：只有日更逐日累积的版本是严格 PIT；回填拿到的是东财*当前*版本配首发日，
早期期间只有一版（见 [schema](schema.md#financial_statement_items)）。

---

## 符号与列过滤

```python
load("daily_bars", symbols=["600519.SH", "000001.SZ"], start="2024-01-01")
load("financial_statement_items", items=["roe"], as_of="2024-06-30")
```

Symbol 格式：`{code}.{SH|SZ|BJ}`，与 `domain/symbols.py` 一致。

---

## DuckDB SQL

```bash
asl query --sql "SELECT * FROM instruments LIMIT 5"
```

常用视图：

| 视图 | 说明 |
|------|------|
| `daily_bars` | 未复权 |
| `daily_bars_hfq` | 后复权价列 |
| `daily_bars_qfq` | 前复权价列 |
| `daily_bars_adj` | 含 adj_* 与 adj_is_exact |
| `{dataset}` | 各 curated/derived 数据集 |

数据库：`{data.root}/duckdb/ashare-lake.duckdb`（只读连接）。

---

## 直读 Parquet

不依赖本项目运行时：

```python
import polars as pl
pl.scan_parquet("data/ashare-lake/curated/daily_bars/**/*.parquet")
```

需自行实现复权与 universe 逻辑；生产推荐 `load()`。

---

## 分区裁剪

`query/parquet_scan.py` 按 `partition_col` 与日期范围裁剪 Hive 分区目录。大窗口查询优先 `scan()` + lazy 算子链。

---

## 错误处理

| 异常 | 常见原因 |
|------|----------|
| `ReaderError: unknown dataset` | 名称拼写或数据集未注册 |
| `ReaderError: no parquet data` | 未 init/compact 或路径错误 |
| `ReaderError` (strict_adj) | 缺 adj_factors 覆盖 |
| PIT 无 `as_of` | 可能包含未来公告（不推荐） |

---

## 相关文档

- [Python API 参考](../reference/python-api.md)
- [ADR-0004](../adr/0004-store-hfq-derive-qfq-at-query.md)
- [query 模块](../modules/query.md)
