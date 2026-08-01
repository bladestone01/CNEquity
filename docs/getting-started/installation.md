# 安装

## 系统要求

| 项 | 要求 |
|----|------|
| Python | ≥ 3.10 |
| 操作系统 | macOS / Linux / **Windows 10+（64-bit）** |
| 磁盘 | 全量 init（2016 起）约需数十 GB，视数据集范围而定 |
| 网络 | 采集需访问 TDX 行情服务器与各 HTTP 数据源 |

Windows 说明：

- 支持原生 Win10/11 + PowerShell / cmd；CI 有 `windows-latest` 单元测试。
- 范围是 **64-bit x86-64**；32-bit 与 ARM64 Windows 未验证。
- WSL 可作为过渡，但不是必需——原生 Windows 已可用。
- 依赖（duckdb / polars / pyarrow 等）均有 `win_amd64` 轮子；若某包退化成从源码编，`asl doctor` 会报出。

## 从 PyPI 安装（推荐）

```bash
pip install ashare-lake
asl demo    # 一分钟真数样例，不需要先 clone 仓库
```

**没有 extras**。一条命令装齐所有数据源——通达信协议（内置客户端）、东方财富、新浪、巨潮、中国人民银行、Baostock、SnowNLP，以及申万/国证成分表所需的 XLS 解析。

旧文档里的 `pip install "ashare-lake[tdx]"` 之类仍然可用，装出来的结果完全一致——pip 会提示一句 `does not provide the extra 'tdx'` 然后照常安装，uv 则不作声。

全量 `asl init` 前先写出配置（不必 clone 仓库）：

```bash
asl config init                   # → configs/ashare-lake.toml；data.root 写为绝对路径；macOS / Windows 自动 workers=1
asl config init --data-root /path/to/lake   # 可选：直接指定 data.root（同样会 resolve 为绝对路径）
asl config validate
```

### Windows（PowerShell / cmd）

路径用正斜杠、反斜杠或盘符均可；`asl config init --data-root` 会把反斜杠正确转义进 TOML：

```powershell
pip install ashare-lake
asl doctor
asl config init --data-root D:/ashare-lake
# 或：asl config init --data-root "D:\ashare-lake"
asl demo
asl query --config configs/ashare-lake.demo.toml --sql "SELECT count(*) FROM daily_bars"
```

> PowerShell 5.1 不支持 `&&`。请分行执行，或用 PowerShell 7+ / cmd。

## 从源码安装（开发）

```bash
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip   # PEP 735 --group 需要 pip >= 25.1
pip install -e . --group dev
# 或：uv sync
```

Windows（PowerShell）：

```powershell
git clone https://github.com/rootSunc/ashare-lake.git
cd ashare-lake
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e . --group dev
```

## 依赖构成

所有运行时依赖都是硬依赖，装完即可跑通日更与回填全流程：

| 包 | 用途 |
|----|------|
| polars、pyarrow、duckdb | 湖存储与查询 |
| httpx、curl_cffi | HTTP 源（东财 / 新浪 / 巨潮） |
| click | CLI |
| baostock | 估值 / ST / 退市行情的历史回填 |
| snownlp | on-demand `stock_news` 情绪（`[sentiment] use_snownlp`） |
| pandas、openpyxl、xlrd | 申万 / 国证成分历史的 XLS·XLSX 解析 |

通达信协议客户端内置于 `adapters/tdx_protocol/_wire`，只用标准库，不引入任何包。

> 曾经的 `tdx` / `macro` / `nlp` / `valuation` / `structure` / `all` extras 已全部移除。带上它们的旧命令不会失败，安装器只会忽略未知 extra（pip 附带一句警告）。

装完建议跑一次体检——它会报出配置与环境不一致（如某个源的包导入失败、`data.root` 写成相对路径）这类静默问题：

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
pytest tests/unit -q                               # 需源码 + --group dev，离线可跑
```

## 依赖版本注意事项

### httpx 不再有上限

早期 `[tdx]` extra 依赖的 `mootdx` 要求 `httpx<0.26`，把整个环境压在 0.25.x。TDX 客户端内置后这个约束消失了：

| 安装方式 | httpx |
|----------|-------|
| `pip install ashare-lake` | 0.28.x |

`pyproject.toml` 里 `httpx>=0.25` 的下界现在只标记「我们用到的 `Client()` 选项最早出现在哪个版本」，不再是为了迁就别人。

### 从 0.3.x 升级：mini-racer 已成为孤儿包

0.4 之前 `akshare` 是硬依赖，它带进来一个编译型 V8 绑定 `mini-racer`。AkShare
已移除（见 [issue #3](https://github.com/rootSunc/ashare-lake/issues/3)），但
pip 与 uv 都不会删除「不再被依赖」的包，所以升级后的环境里 `mini-racer` 会留下来。

它对本项目已无任何作用，可以直接卸载：

```bash
pip uninstall mini-racer
```

若环境里同时还残留更早的 `py-mini-racer`（来自 0.2.x 时代的 `mootdx`），两者会
在共享的 `py_mini_racer/` import 包上互相覆盖。这曾由 `asl doctor --fix` 处理；
现在本项目两个包都不用，直接把它们一起卸掉即可：

```bash
pip uninstall py-mini-racer mini-racer
```

## 下一步

- [快速开始](quickstart.md) — 首次 init 与日更
- [配置参考](configuration.md) — 调优 workers、TDX 服务器、调度组
