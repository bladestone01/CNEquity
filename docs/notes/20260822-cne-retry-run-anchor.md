# 20260822-cne-retry-run-anchor

## 结论

`cne retry --run-id` 只重试 manifest 里的失败批，批次窗口取自 manifest 记录；日期锚定的非 worker step（`trading_status` 等）应沿用 run 记录的 `trade_date`，否则会漂移到"今天"——既白跑（当天未结算/非交易日），更危险的是可能"补错日期"。worker 批（`daily_bars`）窗口仍由批次记录驱动，不受此锚点影响。

## 证据/出处

- `engine.py::_retry_run_locked`：解析 `run_meta["trade_date"]` 优先于调用方/`shanghai_today()`（本会话修复）
- 实测：`--trade-date 2026-08-21` 后 `cne retry` 曾把 `trading_status` 重跑到 2026-08-22（周六、baostock 未结算）

## 状态: promising