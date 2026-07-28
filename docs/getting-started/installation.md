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
pip install ashare-lake
asl demo    # 一分钟真数样例，不需要先 clone 仓库
```

通达信协议行情（日线、指数、除权、证券列表）由内置客户端提供，**不需要任何额外依赖**。历史文档里的 `pip install "ashare-lake[tdx]"` 仍然可用——`tdx` 保留为空 extra——但已无必要。

全量 `asl init` 前先写出配置（不必 clone 仓库）：

```bash
asl config init                   # → configs/ashare-lake.toml；macOS 自动 workers=1
asl config init --data-root /path/to/lake   # 可选：直接写 data.root
asl config validate
```

## 从源码安装（开发）

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# 或：uv sync --extra dev
```

## 可选依赖

| Extra | 包 | 用途 |
|-------|-----|------|
| `valuation` | baostock ≥ 0.8 | `valuation_metrics` 历史回填；`trading_status` ST 历史回填 |
| `macro` | akshare ≥ 1.14 | 补充 PMI、M2、社融等月度宏观指标 |
| `nlp` | snownlp ≥ 0.12 | `sentiment_scores` / `stock_news` NLP 增强 |
| `structure` | pandas、openpyxl、xlrd | 申万行业分类历史 XLS（`industry_members` 回填路径） |
| `all` | 以上全部（不含 `dev`） | 全量日更所需的运行时依赖一次装齐 |
| `dev` | pytest, ruff, pytest-cov, pytest-timeout | 开发与测试 |
| ~~`tdx`~~ | 空 | 已废弃：TDX 客户端已内置，保留仅为兼容旧安装命令 |

```bash
# PyPI —— 全量日更推荐
pip install "ashare-lake[all]"

# 或按需挑选
pip install "ashare-lake[valuation,macro,nlp,structure]"

# 源码可编辑
pip install -e ".[all,dev]"
```

装完建议跑一次体检——它会报出「配置启用了某个源但包没装」这类静默失效：

```bash
asl doctor
```

选型犹豫（本项目 vs AkShare / Tushare）见 [comparison.md](../comparison.md)。
运行前请阅读 [legal-and-data-sources.md](../legal-and-data-sources.md)。

## 配置初始化

```bash
asl config init
# 等价于从包内模板写出 configs/ashare-lake.toml
# 仓库开发也可：cp configs/ashare-lake.example.toml configs/ashare-lake.toml
# 编辑 data.root — 生产环境建议使用绝对路径
```

`configs/ashare-lake.toml`、`data/`、根目录 `logs/` 均已 gitignore，请勿强制加入版本库。

## 验证安装

```bash
asl --help
asl demo
# 全量配置就绪后：
asl config validate --config configs/ashare-lake.toml
asl servers test --config configs/ashare-lake.toml   # 探测 TDX 行情主机
pytest tests/unit -q                               # 需源码 + [dev]，离线可跑
```

## 依赖版本注意事项

### httpx 不再有上限

早期 `[tdx]` extra 依赖的 `mootdx` 要求 `httpx<0.26`，把整个环境压在 0.25.x。TDX 客户端内置后这个约束消失了：

| 安装方式 | httpx |
|----------|-------|
| `pip install ashare-lake` | 0.28.x |
| `pip install "ashare-lake[all]"` | 0.28.x |

`pyproject.toml` 里 `httpx>=0.25` 的下界现在只标记「我们用到的 `Client()` 选项最早出现在哪个版本」，不再是为了迁就别人。

### py_mini_racer 包名冲突（历史问题，干净安装已不会遇到）

`akshare` 依赖 `mini-racer`，而已移除的 `mootdx` 依赖 `py-mini-racer`——**两个不同的发行包，却都往同一个 import 包 `py_mini_racer/` 里装文件**。安装器不拦截这种重叠，后装的覆盖先装的，可能留下「加载器与原生二进制不匹配」（`dlsym: symbol not found`），且结果取决于安装顺序。

本项目不再依赖 mootdx，所以**干净环境装不出这个冲突**。仍可能遇到的情况只有两种：

- 从旧版本升级上来，环境里还残留 `py-mini-racer`
- 你出于自己的原因另行安装了 mootdx

`asl doctor` 会检测这个状态，`--fix` 直接修好：

```bash
asl doctor --fix
```

它在 macOS / Linux / Windows 上行为一致：不走 shell，直接以 argv 调用当前解释器的安装器（有 pip 用 `python -m pip`，`uv venv` 建的无 pip 环境自动改用 `uv pip`）。doctor 也会把等价的手动命令按行打印出来。

> 手动执行时不要把两条命令用 `&&` 串起来——那在 Windows PowerShell 5.1（多数 Windows 的默认 shell）是语法错误。同时**两步缺一不可**：两个发行包在 `__init__.py` 上重叠，只卸载 `py-mini-racer` 会连带删掉 `mini-racer` 需要的文件。

## 下一步

- [快速开始](quickstart.md) — 首次 init 与日更
- [配置参考](configuration.md) — 调优 workers、TDX 服务器、调度组
