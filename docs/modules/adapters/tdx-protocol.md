# tdx_protocol 适配器

路径：`src/ashare_lake/adapters/tdx_protocol/`

通过**内置的通达信协议客户端**连接行情服务器（无需本地通达信客户端）。A 股日线、指数、证券列表、除权等的核心主源。

**依赖**：无。协议实现内置于 `_wire/`，仅用标准库。

---

## 文件

| 文件 | 职责 |
|------|------|
| `client.py` | 连接管理、服务器探测、`fetch_*` 入口 |
| `quotes.py` | `_wire` 之上的门面：市场推导、翻页、`vol`→`volume` 别名 |
| `hosts.py` | 内置兜底行情主机列表 |
| `_wire/` | 内置的 TDX 线协议实现（源自 tdxpy，MIT，见 `LICENSE.tdxpy`） |
| `bars.py` | 分页日线/指数 K 线 |
| `corporate_actions.py` | 每股 xdxr → corporate_actions 行 |
| `__init__.py` | 导出 |

---

## 连接与服务器选择

`[tdx_protocol]`：

- `servers = "auto"`：先并行探测 `[tdx_protocol.hosts].standard`，再 fallback `hosts.py` 内置列表
- `servers = "host:port"`：固定单服
- `allow_mock = false`（生产）：连接失败抛异常，不造假数据

`asl servers test` 验证连通性。

---

## 主要 API

| 函数 | 数据 |
|------|------|
| `fetch_instruments(cfg)` | 全市场证券列表 |
| `fetch_trading_calendar(cfg, start, end)` | 交易日（辅助） |
| `fetch_daily_bars(cfg, symbol, start, end)` | 未复权日线 |
| `fetch_index_bars(cfg, symbol, start, end, frequency)` | 指数 K 线 |
| `fetch_corporate_actions(cfg, symbol)` | 除权除息 |
| `fetch_trading_status(cfg)` | 停牌列表（辅助） |

---

## 分页与限制

- TDX 协议单次最多约 **800** 条 K 线（`_wire.MAX_PAGE`）
- `bars.py` 循环分页；失败即暴露，不静默截断
- 日更增量：检测水位后早停，避免每日翻全历史

---

## 为什么内置协议实现

原先依赖 `mootdx`（它又依赖 `tdxpy`）。两者同属一个作者，均于 2024 年后停止发布；`mootdx` 还锁死 `httpx<0.26`，并引入 `py-mini-racer`，与 `akshare` 的 `mini-racer` 争抢同一个 import 包。上游没有可修复的版本。

`_wire/` 从 tdxpy 裁剪出本项目实际用到的 5 个标准市场调用（1618 行 / 原 4929 行），砍掉本地文件 reader、财务爬虫、扩展市场与 pandas 依赖。TDX 线协议是冻结的传统二进制格式，内容是定长 `struct.unpack`，不随上游变动。

迁移时以真实服务器逐字节对拍验证过：解析结果与上游 tdxpy 完全一致，门面输出与 mootdx 完全一致（含 51478 行全量证券列表零差异）。`tests/unit/test_tdx_decoupling.py` 持续守卫，防止依赖回流。

---

## 主备角色（Failover）

| 数据集 | 角色 |
|--------|------|
| daily_bars | **主源** |
| corporate_actions | 回填主源；日更时东财为主、TDX 写 snapshot |

---

## 多进程注意

TDX 连接**不可**跨 fork 共享（socket + 心跳线程）。`worker_pool` 在每个子进程内新建连接。

---

## 相关文档

- [配置 — tdx_protocol](../../getting-started/configuration.md#tdx_protocol)
- [bars step](../steps.md)
