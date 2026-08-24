# 20260822-step-status-channel-failures

## 结论

预期中的业务失败（覆盖缺口、供应商降级）应走 step 状态通道——返回 `status="failed"` + 结构化载荷，不用 `RuntimeError`；异常只留给真正的 bug。收益：不产生误导性 traceback、载荷可被脚本/`cne status` 程序化消费；与仓库既有惯例（收集失败列表返回）一致。

## 证据/出处

- `steps/bars.py::_finish_daily_bars` failed 分支 + `_failed_daily_bar_batch_payload`（本会话实现）
- 仓库惯例：`fetch_bars_via_sina`（收 failed 列表）、`derive_adj_factors`（failed_tasks + status）、`walk_day_backfill`（`status: warning`）
- 设计：`openspec/changes/daily-bars-step-status-channel/design.md` D1

## 状态: promising