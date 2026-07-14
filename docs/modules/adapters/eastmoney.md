# eastmoney 适配器

路径：`src/stock_data_engine/adapters/eastmoney/`

东方财富 HTTP API（datacenter、clist、行情接口等）。资金面、估值快照、结构成员、新闻、ST/停牌等的主要 HTTP 源。

---

## 核心基础设施

| 文件 | 职责 |
|------|------|
| `em_auth.py` | `EastMoneyClient`、NID cookie、请求头 |
| `datacenter.py` | 通用 datacenter API 封装 |
| `clist.py` | 分页 clist（全市场列表类接口） |
| `common.py` | EM 代码 ↔ `symbol` 互转 |

限速：`[sources.eastmoney].min_interval_seconds`（跨进程文件锁）。

---

## 功能模块

| 文件 | 数据集 / 用途 |
|------|----------------|
| `instruments.py` | instruments `list_date`  enrichment |
| `bars.py` | daily_bars **备源**（failover snapshot） |
| `corporate_actions.py` | corporate_actions **daily 主源** |
| `capital.py` | fund_flow, margin_trading, northbound_*, dragon_tiger, block_trades |
| `valuation.py` | valuation_metrics 当日快照 |
| `fundamentals.py` | financial_statement_items |
| `sectors.py` | sector_members |
| `industry.py` | industry_members |
| `index_constituents.py` | index_constituents |
| `trading_status.py` | ST 列表、停牌列表 |
| `institutional.py` | institutional_holdings |
| `consensus.py` | analyst_consensus |
| `share_unlock.py` | share_unlock_schedule |
| `stock_news.py` | stock_news（on-demand / sentiment） |

---

## 语义注意

### snapshot 类

`valuation_metrics`、`fund_flow`、`sector_members` 等接口返回**当前页面快照**，step 用 `trade_date` 打戳写入。历史值不可伪造 — 见 `fetch_semantics="snapshot"`。

历史估值：`valuation_metrics` 的 `backfill_source=baostock`。

### 北向持股

2024-08 起逐日披露变化；持股数据季频为主。`northbound_holdings` 的 `max_staleness_days=100`。

### ST / 停牌

`trading_status` 日更主源；**不提供**长历史 ST，需 baostock 回填 + 派生停牌。

---

## Failover

- `daily_bars`：**备源**（主源 TDX 失败时 snapshot）
- `corporate_actions`：**daily 主源**

---

## 相关文档

- [capital step](../steps.md)
- [数据集目录 — L4/L5](../../datasets/catalog.md)
