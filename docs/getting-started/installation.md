# 安装

## 系统要求

| 项 | 要求 |
|----|------|
| Python | ≥ 3.11 |
| 操作系统 | macOS / Linux（Windows 未正式验证） |
| 磁盘 | 全量 init（2016 起）约需数十 GB，视数据集范围而定 |
| 网络 | 采集需访问 TDX 行情服务器与各 HTTP 数据源 |

## 从 PyPI 安装（推荐）

```bash
pip install "ashare-lake[tdx]"
asl demo    # 一分钟真数样例，不需要先 clone 仓库
```

`[tdx]` extra 安装 `mootdx`，用于通达信协议行情（日线、指数、除权、证券列表等）。不装 `[tdx]` 时 P0 行情类数据集无法采集。

全量 `asl init` 需要一份本地 toml：可从仓库拷贝 `configs/ashare-lake.example.toml`，或按 [configuration](configuration.md) 自写。

## 从源码安装（开发）

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"
# 或：uv sync --extra tdx
```

## 可选依赖

| Extra | 包 | 用途 |
|-------|-----|------|
| `tdx` | mootdx ≥ 0.11 | TDX 协议主行情源（**推荐生产必装**） |
| `valuation` | baostock ≥ 0.8 | `valuation_metrics` 历史回填；`trading_status` ST 历史回填 |
| `macro` | akshare ≥ 1.14 | 补充 PMI、M2、社融等月度宏观指标 |
| `nlp` | snownlp ≥ 0.12 | `sentiment_scores` / `stock_news` NLP 增强 |
| `structure` | pandas、openpyxl、xlrd | 申万行业分类历史 XLS（`industry_members` 回填路径） |
| `dev` | pytest, ruff, pytest-cov, pytest-timeout | 开发与测试 |

```bash
# PyPI
pip install "ashare-lake[tdx,valuation,macro,nlp,structure]"

# 源码可编辑
pip install -e ".[tdx,valuation,macro,nlp,structure,dev]"
```

选型犹豫（本项目 vs AkShare / Tushare）见 [comparison.md](../comparison.md)。
运行前请阅读 [legal-and-data-sources.md](../legal-and-data-sources.md)。

## 配置初始化

```bash
# 在已 clone 的仓库内：
cp configs/ashare-lake.example.toml configs/ashare-lake.toml
# 编辑 data.root — 生产环境建议使用绝对路径
```

`configs/ashare-lake.toml`、`data/`、根目录 `logs/` 均已 gitignore，请勿强制加入版本库。

## 验证安装

```bash
asl --help
asl demo
# 全量配置就绪后：
asl config validate --config configs/ashare-lake.toml
asl servers test --config configs/ashare-lake.toml   # 需 [tdx]
pytest tests/unit -q                               # 需源码 + [dev]，离线可跑
```

## 与 httpx 版本

`pyproject.toml` 将 `httpx` 下界设为 `>=0.25`，以便与 `mootdx`（要求 `httpx<0.26`）共存。未安装 `[tdx]` 时 pip/uv 可选用更新版 httpx。

## 下一步

- [快速开始](quickstart.md) — 首次 init 与日更
- [配置参考](configuration.md) — 调优 workers、TDX 服务器、调度组
