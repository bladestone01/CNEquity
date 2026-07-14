# 安装

## 系统要求

| 项 | 要求 |
|----|------|
| Python | ≥ 3.11 |
| 操作系统 | macOS / Linux（Windows 未正式验证） |
| 磁盘 | 全量 init（2016 起）约需数十 GB，视数据集范围而定 |
| 网络 | 采集需访问 TDX 行情服务器与各 HTTP 数据源 |

## 基础安装

```bash
git clone <repo-url> StockDataEngine && cd StockDataEngine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx]"
```

`[tdx]` extra 安装 `mootdx`，用于通达信协议行情（日线、指数、除权、证券列表等）。不装 `[tdx]` 时 P0 行情类数据集无法采集。

## 可选依赖

| Extra | 包 | 用途 |
|-------|-----|------|
| `tdx` | mootdx ≥ 0.11 | TDX 协议主行情源（**推荐生产必装**） |
| `valuation` | baostock ≥ 0.8 | `valuation_metrics` 历史回填；`trading_status` ST 历史回填 |
| `macro` | akshare ≥ 1.14 | 补充 PMI、M2、社融等月度宏观指标 |
| `nlp` | snownlp ≥ 0.12 | `sentiment_scores` / `stock_news` NLP 增强 |
| `dev` | pytest, ruff, pytest-cov, pytest-timeout | 开发与测试 |

```bash
pip install -e ".[tdx,valuation,macro,nlp,dev]"
```

## 配置初始化

```bash
cp configs/stockdata.example.toml configs/stockdata.toml
# 编辑 data.root — 生产环境建议使用绝对路径
```

`configs/stockdata.toml` 已 gitignore，不会提交到仓库。

## 验证安装

```bash
sde config validate --config configs/stockdata.toml
sde servers test --config configs/stockdata.toml   # 需 [tdx]
pytest tests/unit -q                               # 需 [dev]，离线可跑
```

## 与 httpx 版本

`pyproject.toml` 将 `httpx` 下界设为 `>=0.25`，以便与 `mootdx`（要求 `httpx<0.26`）共存。未安装 `[tdx]` 时 pip/uv 可选用更新版 httpx。

## 下一步

- [快速开始](quickstart.md) — 首次 init 与日更
- [配置参考](configuration.md) — 调优 workers、TDX 服务器、调度组
