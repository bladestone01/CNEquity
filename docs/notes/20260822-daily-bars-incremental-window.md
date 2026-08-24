# 20260822-daily-bars-incremental-window

## 结论

daily_bars 抓取窗口由 watermark 驱动：有水位 → `[watermark+1, trade_date]`；无水位 → `trade_date − 5 天`回看（`INCREMENTAL_LOOKBACK_DAYS=5`）。跨 run 每次全量重抓该窗口并写入新 run_id 的 staging（parquet），compact 按 PK `keep=last` 幂等；"按缺失符号增量更新 parquet"**只存在于同一 run 的 `cne retry`**（`failed_scope` + attempt 级文件）。

## 证据/出处

- `steps/common.py:42` `incremental_window`（`min(watermark+1, trade_date)` / `trade_date−5`，common.py:21）
- `storage/parquet.py` `compact_dataset`（跨文件 PK 去重）
- 实测：datalake `daily_bars watermark = None` → 窗口落为 `2026-08-16..2026-08-21`

## 状态: promising