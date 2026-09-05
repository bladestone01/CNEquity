## 1. 状态通道改造（capability: daily-bars-failure-status-channel）

- [x] 1.1 `steps/bars.py::_finish_daily_bars`：多日窗口未解析缺口不再 `raise RuntimeError`——返回 `{"status": "failed", "unresolved_symbols": [...], "missing_keys": n, "failed_batches": [...], "rows_read/written": ..., "context_updates": {...}}`；保留真 bug（schema/不变量/gap-fill 异常）仍 raise
- [x] 1.2 组装 `failed_batches`：按 `{batch_id, symbol_count, sample_symbols}` 结构化（新增 `_failed_daily_bar_batch_payload`），`_describe_failed_daily_bar_batches` 改为复用它
- [x] 1.3 `logger.error` 可操作失败行：缺口数 + 未解析符号样例 + `failed batches: <batch_id>: N symbol(s)` + `→ run \`cne retry --run-id <id>\`` 指引（ERROR 级别，`--quiet` 不吞）
- [x] 1.4 确认 `engine._run_step` 对 worker step 返回 `status="failed"` 的透传：`_run_step` 已 `out.pop("status")` 并回传，`merge_result` 置 `had_error` → run `failed`（引擎层测试 2.3 锁定）
- [x] 1.5 确认 `_has_partial_failures` 不会把显式 `status="failed"` 覆盖/降级为 warning；`_merge_ownership_result` 加守卫：显式 failed 不被 delegated 不完整降为 warning

## 2. 测试

- [x] 2.1 迁移既有测试：`test_finish_daily_bars_reports_failed_status_with_payload`——断言返回 `status=="failed"` + `unresolved_symbols`/`missing_keys`/`failed_batches` 内容与样例正确
- [x] 2.2 新增：`test_finish_daily_bars_genuine_bug_still_raises`——gap-fill 真异常照样上抛
- [x] 2.3 新增：`test_engine_run_job_surfaces_worker_step_failed_status`——run 结果为 `failed`、`results[step=daily_bars]` 携带结构化字段
- [x] 2.4 新增：同 2.1 用 caplog 锁定——含 `cne retry --run-id` 的 ERROR 行且无 `Traceback (most recent call last)` 帧
- [x] 2.5 回归：同 2.1 断言 unresolved 批仍 manifest `failed` → `incomplete_batch_counts_by_dataset == {"daily_bars": 1}`（compact gate 阻塞）；重试 scope 由既有 gate/retry 测试覆盖

## 3. 验证与交付

- [x] 3.1 `ruff check src tests` 全绿 + `pytest tests/unit -q` 全量通过（2082 passed）
- [x] 3.2 CHANGELOG 记录本次变更（daily_bars 缺口失败改走 step 状态通道 + 结构化字段 + 无 traceback 日志）
- [x] 3.3 冒烟（可选，需 datalake）：构造未解析缺口确认 `cne run daily` 输出 `{"status": "failed"}`、日志含重试指引、无 traceback