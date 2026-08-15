# 20260815-daily-bars

> promoted → 权威源：`agents/knowledges/daily-bars.md`（20260815 折叠为速查，凭据非事实源）

## 结论（1-2 行）

- `daily_bars` 存全市场股票 + ETF/LOF（不含指数）的**未复权**日 K，主键 `(symbol, trade_date)`；数据只有 `data_version=v2` 的行才保证 `volume` 单位是**股**，v1 行是手，抓取端靠 adapter 换算。

## 证据/出处

- `daily-bars` 讨论结论（20260815 会话），速查版已沉淀：`agents/knowledges/daily-bars.md`
- 权威列定义：`docs/datasets/schema.md:97`、`src/ashare_lake/domain/schemas.py:34`
- 停牌约定（`volume=0`/`amount=0`）：`src/ashare_lake/steps/bars.py`（pre-open 占位整版 OHLX 相等被判 fail，`_reject_preopen_placeholder`，bar.py:438）

<!-- capture #2（同 topic append，20260815） -->

## 结论（追加 1-2 行）

- 复权查询一律走 `daily_bars_adj` 视图（hfq/qfq 因子关联），不要手写 glob 拼复权价。

## 证据/出处

- `agents/knowledges/daily-bars.md`「易踩的坑」末条（004 会话补记）

## 状态: promising