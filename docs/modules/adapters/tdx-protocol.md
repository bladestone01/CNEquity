# tdx_protocol 适配器

路径：`src/ashare_lake/adapters/tdx_protocol/`

通过 **mootdx** 连接通达信行情服务器（无需本地通达信客户端）。A 股日线、指数、证券列表、除权等的核心主源。

**依赖**：`pip install -e ".[tdx]"`

---

## 文件

| 文件 | 职责 |
|------|------|
| `client.py` | 连接管理、服务器探测、`fetch_*` 入口 |
| `bars.py` | 分页日线/指数 K 线 |
| `corporate_actions.py` | 每股 xdxr → corporate_actions 行 |
| `__init__.py` | 导出 |

---

## 连接与服务器选择

`[tdx_protocol]`：

- `servers = "auto"`：先并行探测 `[tdx_protocol.hosts].standard`，再 fallback mootdx 内置列表
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

- mootdx 单次最多约 **800** 条 K 线
- `bars.py` 循环分页；失败即暴露，不静默截断
- 日更增量：检测水位后早停，避免每日翻全历史

---

## 主备角色（Failover）

| 数据集 | 角色 |
|--------|------|
| daily_bars | **主源** |
| corporate_actions | 回填主源；日更时东财为主、TDX 写 snapshot |

---

## 多进程注意

mootdx 连接**不可**跨 fork 共享。`worker_pool` 在每个子进程内新建连接。

---

## 相关文档

- [配置 — tdx_protocol](../../getting-started/configuration.md#tdx_protocol)
- [bars step](../steps.md)
