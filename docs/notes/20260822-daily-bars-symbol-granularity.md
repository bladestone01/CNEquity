# 20260822-daily-bars-symbol-granularity

## 结论

daily_bars 失败归因到符号级（默认 `symbol` 模式）：部分成功符号立即落盘，失败只记符号×日期缺口并精确重拉；"不让缺口被当成完整"的保障是 manifest `failed` 批 → compact gate 锁住 daily_bars、水位线不推进，而非靠抛异常。`batch` 为 legacy 整批 all-or-nothing 的配置回退（默认关闭）。

## 证据/出处

- `worker_pool.py::_stage_daily_bars_batch` / `client.py::fetch_daily_bars_tolerant`（单会话逐符号容错）
- `compact_gate.py:34`（`incomplete_batch_counts_by_dataset` 按批状态锁 dataset）
- 设计：`openspec/changes/daily-bars-tip-stock-universe/design.md` D2-D6
- 实测：2026-08-20..08-21 backfill 中 EastMoney "Server disconnected" 时，健康符号照常落盘、缺口按符号进入 `failed_scope_json`

## 状态: promising