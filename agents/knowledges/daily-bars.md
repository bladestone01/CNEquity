# daily_bars 速查

**问题：`daily_bars` 存什么？何时触发存储？** 权威源：`docs/datasets/schema.md` + `src/ashare_lake/steps/bars.py`。

## 存什么

- 全市场股票 + ETF/LOF（**不含指数**，index 走 `index_bars`）的**未复权**日 K。
- 列：`symbol / trade_date / open / high / low / close / volume / amount` + 溯源列 `source / data_version / fetched_at`。
- `volume` 一律 **股**（`data_version=v2` 才保证；TDX 日线原生是手，adapter ×100）。
- `amount` 人民币元，恒等式 `amount ≈ close × volume` 是单位检查的锚。
- 停牌日约定：OHLCV 仍有值但 `volume=0`、`amount=0`。
- 主键 `(symbol, trade_date)`，按日分区。
- 权威列定义：`docs/datasets/schema.md:97`、`src/ashare_lake/domain/schemas.py:34`。

## 何时触发写入（先入 staging，compact 提升进 curated）

| 路径 | 触发 | 位置 |
|---|---|---|
| 每日增量（主路） | `asl run daily` → `step_daily_bars`，按水位增量到 `trade_date`，经 TDX 协议逐 symbol | `src/ashare_lake/steps/bars.py:39` |
| 历史回补 | `asl backfill daily_bars --start/--end` | 同一 step，走 backfill 分支 |
| 深度回溯 2001–2015 | `daily_bars_history`（同花顺原始价） | `src/ashare_lake/steps/bars.py:576` |
| 退市股补史（survivorship） | `daily_bars_delisted`（baostock） | `src/ashare_lake/steps/bars.py:768` |
| TDX 缺 tip key | 东财 **clist** 补丁（ADR-0005 路由） | `src/ashare_lake/steps/bars.py:185` |
| TDX 多日批次失败 | 东财 **kline** 补丁 | `src/ashare_lake/steps/bars.py:324` |
| 北交所（TDX 无路由） | Sina 兜底 | `src/ashare_lake/steps/bars.py:476` |

## 易踩的坑

- **pre-open 占位**：开盘前抓的整版 OHLX 相等、volume=0，占比 ≥50% 直接 fail（`_reject_preopen_placeholder`，bar.py:438）。
- **单位**：只有 v2 行保证 `volume` 是股；v1 行 `tdx_protocol/sina` 是手。
- 查询复权：读 `daily_bars_adj` 视图（hfq/qfq 因子关联），别手写 glob。