# 快速开始

本指南覆盖从零到可读数据的完整路径。详细选项见 [CLI 参考](../reference/cli.md)。

## 1. 准备

完成 [安装](installation.md) 后确认：

```bash
source .venv/bin/activate
cp configs/ashare-lake.example.toml configs/ashare-lake.toml
asl config validate
```

## 2. 初始化数据湖

```bash
asl init --config configs/ashare-lake.toml
```

`init` 会：

1. 创建 `{data.root}` 下 staging / curated / derived / meta / duckdb 目录
2. 初始化 `meta/manifest.db`（SQLite WAL）与 DuckDB 视图
3. 按 `[job.init.phases]` 执行分阶段全量回填（默认自 2016 年起）

**仅建目录、不跑回填：**

```bash
asl init --layout-only --config configs/ashare-lake.toml
```

**中断后续跑：**

```bash
asl init --resume --config configs/ashare-lake.toml
# 或指定 run_id
asl retry --run-id <run_id> --config configs/ashare-lake.toml
```

init 耗时较长（全市场日线分页回填），建议在稳定网络下运行。阶段定义见 [数据流 — Init](../architecture/data-flow.md#init-全量回填)。

## 3. 回填验收（推荐）

```bash
.venv/bin/python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# 同窗口重跑 daily 后对比
.venv/bin/python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

验收项：幂等性、覆盖起点、消费层可读。详见 [回填完成验收](../operations/runbook.md#回填完成验收)。

## 4. 每日增量

```bash
asl run daily --config configs/ashare-lake.toml
```

非交易日自动跳过（`skipped_non_trading_day`，退出码 0）。

**按调度组分批跑（与生产 pipeline 一致）：**

```bash
asl run daily --group core --config configs/ashare-lake.toml
asl run daily --group capital --config configs/ashare-lake.toml
# signals / fundamentals / macro_risk / research
```

每组末尾含 `compact`，数据会写入 curated。组定义见 [配置 — 调度组](configuration.md#调度组)。

## 5. 查看状态

```bash
asl status --config configs/ashare-lake.toml              # 最近一次 run 摘要
asl status --datasets --config configs/ashare-lake.toml   # 各数据集新鲜度
asl catalog --config configs/ashare-lake.toml             # 行数统计
```

## 6. 读取数据

### Python API（推荐）

```python
from ashare_lake.query import load

bars = load(
    "daily_bars",
    start="2024-01-01",
    end="2024-12-31",
    adjust="hfq",
    universe="all_a",
)

roe = load(
    "financial_statement_items",
    items=["roe"],
    as_of="2024-04-30",
)
```

见 [查询指南](../datasets/query-guide.md) 与 [Python API](../reference/python-api.md)。

### DuckDB SQL

```bash
asl query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/ashare-lake.toml
```

数据库文件：`{data.root}/duckdb/ashare-lake.duckdb`。

### 直读 Parquet

```python
import polars as pl
df = pl.scan_parquet("data/ashare-lake/curated/daily_bars/**/*.parquet")
df.filter(pl.col("symbol") == "600519.SH").collect()
```

## 7. 失败重试

```bash
asl status --config configs/ashare-lake.toml    # 找到 failed run_id
asl retry --run-id <run_id> --config configs/ashare-lake.toml
```

retry 只重跑失败 batch；全部成功后自动 compact → derive_adj_factors → audit。

## 8. 生产调度（可选）

```bash
scripts/install_scheduler.sh   # macOS launchd，每天 16:05
```

见 [运维 Runbook](../operations/runbook.md)。

## 常见陷阱

| 问题 | 说明 |
|------|------|
| `load()` 读不到新数据 | 确认 run 已 compact；分组 run 必须含 `compact` step |
| `universe="all_a"` 未剔历史 ST | `trading_status` 仅覆盖日更起点之后；2016→上线日回测需注意 |
| init 中途失败 | 勿重新 `init`，用 `--resume` 或 `retry` |
| TDX 连接失败 | `asl servers test`；检查 `[tdx_protocol.hosts]` 与网络 |
