# 快速开始

本指南覆盖两条路径：

1. **一分钟试玩**（推荐新手）：`asl demo`，小宇宙、独立目录，几分钟出真数  
2. **全量数据湖**：`asl config init` → `asl init` → `asl run daily`（耗时长、占磁盘）

详细选项见 [CLI 参考](../reference/cli.md)。安装见 [installation](installation.md)。

## 0. 一分钟试玩（可选）

不必 clone 仓库：

```bash
pip install ashare-lake
asl demo
# 可选：再看一根完整 1m 会话
# asl demo --intraday
```

会写入独立的 `data/ashare-lake-demo/` 与 `configs/ashare-lake.demo.toml`。  
**不要**把 demo 的 `data_root` 拿去跑全量 `asl init`。

接着可查：

```bash
asl query --config configs/ashare-lake.demo.toml --sql "
  SELECT symbol, trade_date, close, volume, source
  FROM daily_bars
  ORDER BY trade_date DESC
  LIMIT 10
"
```

只想验证复权研究口径，不必初始化全市场：

```bash
asl demo --research --symbols 600519.SH
```

research demo 会把窗口扩展到约三年，读取 Sina 的 hfq 因子，并打印 raw return 与 hfq return 的对照。
它需要额外访问 Sina；网络受限时，先使用不带 `--research` 的基础 demo。

下面从第 1 步起是全量湖路径。

## 1. 准备全量配置

```bash
pip install ashare-lake   # 若尚未安装
asl config init                 # → configs/ashare-lake.toml；macOS / Windows 自动 workers=1
# 可选：asl config init --data-root /abs/path/to/lake
asl config validate
```

按需编辑 `configs/ashare-lake.toml` 里的 `data.root`（生产建议绝对路径）。

> 源码开发：也可 `cp configs/ashare-lake.example.toml configs/ashare-lake.toml`，与 `asl config init` 等价。

## 2. 初始化数据湖

```bash
asl init --config configs/ashare-lake.toml
```

`init` 会：

1. 创建 `{data.root}` 下 staging / curated / derived / meta / duckdb 目录  
2. 初始化 `meta/manifest.db`（SQLite WAL）与 DuckDB 视图  
3. 按 `[job.init.phases]` 执行分阶段全量回填（默认最近 3 年、全市场标的）

需要从 2016 年起的完整初始化时，使用 `asl init --profile full`；也可以先用默认窗口建湖，再按需回填。

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

init 耗时较长（全市场日线分页回填），建议在稳定网络下运行。阶段定义见 [数据流 — Init](../architecture/data-flow.md#init全量回填)。

## 3. 回填验收（推荐，需仓库脚本）

验收脚本在 GitHub 仓库的 `scripts/`，**不随 PyPI 包安装**。有 checkout 时：

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# 同窗口重跑 daily 后对比
python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

验收项：幂等性、覆盖起点、消费层可读。详见 [回填完成验收](../operations/runbook.md#回填完成验收)。

纯 PyPI 用户可先用 `asl status --datasets` / `asl catalog` 做粗检。

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

## 8. 生产调度（可选，需仓库脚本）

```bash
# 需 clone 仓库后：
scripts/install_scheduler.sh   # macOS launchd，Helsinki 每天 11:15
```

见 [运维 Runbook](../operations/runbook.md)。

## 常见陷阱

| 问题 | 说明 |
|------|------|
| `load()` 读不到新数据 | 确认 run 已 compact；分组 run 必须含 `compact` step |
| `universe="all_a"` 未剔历史 ST | `trading_status` 仅覆盖日更起点之后；2016→上线日回测需注意 |
| init 中途失败 | 勿重新 `init`，用 `--resume` 或 `retry` |
| TDX 连接失败 | `asl servers test`；检查 `[tdx_protocol.hosts]` 与网络 |
| 缺配置报错 | 先跑 `asl config init` |
| demo 与全量混用 | demo 用独立 `data/ashare-lake-demo/`，全量另配 `data.root` |

更多排障：[troubleshooting](../operations/troubleshooting.md) · [runbook](../operations/runbook.md)。
