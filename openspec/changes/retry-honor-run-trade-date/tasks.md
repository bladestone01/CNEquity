## 1. trade_date 锚点还原（capability: retry-run-date-anchoring）

- [x] 1.1 `engine.py::_retry_run`：解析 `run_meta.get("trade_date")`，存在且 `date.fromisoformat` 可解析时覆盖 `trade_date` 参数后转入 `_retry_run_locked`；非法值捕获并保留传入值（防 retry 崩溃）
- [x] 1.2 确认透传：`_retry_run_locked` → `_run_step(dataset, trade_date, ...)` 与 `_merge_retry_context` 的 `context["trade_date"]` 使用解析后的值；worker 批 `BatchSpec` 窗口逻辑零改动
- [x] 1.3 兼容性声明：无 `trade_date` 记录的旧 run 回退 `shanghai_today()`，行为与现状一致；`_backfill`/`_backfill_scope` 还原逻辑不变

## 2. 测试

- [x] 2.1 单测：run 记录 08-21、系统日期 08-22 → `_retry_run` 解析后 trade_date 为 08-21（日期锚定 step 重跑原日期）
- [x] 2.2 单测：run 无 trade_date 记录 → 回退调用方传入/`shanghai_today()`
- [x] 2.3 单测：run 的 trade_date 非法字符串 → 回退传入值，不 raise
- [x] 2.4 回归：worker 批（daily_bars）重试窗口仍来自 manifest `window_start/end`，不受新逻辑影响

## 3. 验证与交付

- [x] 3.1 `ruff check src tests` 全绿 + `pytest tests/unit -q` 全量通过
- [x] 3.2 CHANGELOG 记录本次变更（cne retry 重放 run 记录的 trade_date，日期锚定 step 不再漂移到今天）
- [ ] 3.3 冒烟（可选，需 datalake）：`--trade-date 2026-08-21` 后 `cne retry` 不再出现 08-22 的 trading_status 抓取