# 20260904-daily-bars-batch-failover

## 结论

worker_pool 进度中的 `(100 symbols FAILED)` 仅表示单个 100 标的批次主源拉取异常并被隔离记录，任务继续推进；系统立即触发备源（EastMoney）落盘 backup snapshot，并在收尾阶段经 gap-fill 填补与停牌合规豁免，不代表整体任务失败。

## 证据/出处

- `worker_pool.py:473-487`（`_progress` 捕获单个 batch 异常打印 FAILED 进度，池子继续处理后续批次）
- `worker_pool.py:643-656`（ProcessPool 异常捕获并记录 `failed_symbols`）
- `failover.py:51-57`（`write_backup_snapshot` 备源快照落盘）
- `steps/bars.py:874-989, 1050-1100`（`_finish_daily_bars` 备源 gap-fill 填补与 `_certify_missing_daily_symbols` 证明豁免）
- 实测日志：`Wrote backup snapshot daily_bars source=eastmoney rows=267` 与 `72/74 batches (100 symbols FAILED)` 并存

## 状态: promising
