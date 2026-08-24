## Why

`_finish_daily_bars` 在多日窗口存在未解析缺口时 `raise RuntimeError("daily_bars: one or more symbol batches failed")`。这是**预期中的业务结果**（断网/真缺口），不是未预期故障，但用 `RuntimeError` 表达带来三个问题：① 语义错位——异常通道本应留给真正的 bug；② 信息无结构——符号/批次/缺口数只能塞进 message 字符串，自动化与 `cne status` 无法程序化解析；③ 呈现噪音——触发 `logger.exception` 打印整段 traceback，让"已知待重试"看起来像崩溃。数据安全（compact gate 锁 daily_bars、水位线不推进、批仍是 manifest `failed`）无论是否 raise 都已成立，异常只是重复防御且附带干扰。

## What Changes

- **缺口失败改走 step 状态通道**：`_finish_daily_bars` 对未解析的多日缺口**不再 raise**，改为返回 `status: "failed"` + 结构化载荷（`unresolved_symbols`、`failed_batches`（batch_id + 符号数 + 样例）、`missing_keys`、`rows_read/written`）；`step_daily_bars` 透传，`engine._run_step` 已原生支持 worker step 的 `status∈{success, warning, failed}`。
- **日志降噪 + 可操作提示**：失败时打印一条 `logger.error`（缺口符号数、失败批次、`cne retry --run-id` 指引），引擎改为 `Step daily_bars failed in …s` 无 traceback 日志；异常通道只留给真正的 bug。
- **严格性不变**：run 仍以 `status="failed"` 收尾、exit 1；unresolved 批仍为 manifest `failed` → compact gate 照常锁 daily_bars；`cne retry --run-id` 行为不变（批状态驱动，与 run 状态通道正交）。
- **持久依据不变**：`failed_scope_json`（符号×日期）与 `daily_bars_kline_gapfill` audit findings 仍是审计与重试的唯一持久来源。
- `cne run daily` 简明控制台输出不变（`run_id`/`status`）；完整结构化字段随引擎 run 结果可消费（`cne backfill` 全量 JSON / 脚本）。

## Capabilities

### New Capabilities

- `daily-bars-failure-status-channel`: daily_bars 的多日缺口失败从"抛 RuntimeError"改为"step 状态通道 + 结构化载荷"——`_finish_daily_bars` 返回 `status=failed` 及 `unresolved_symbols`/`failed_batches`/`missing_keys`；引擎/日志转为非 traceback 的可操作呈现；隐藏 strict 语义（run failed、compact gate 锁住、retry 按批）保持不变。

### Modified Capabilities

<!-- 既有 daily-bars 能力的 spec 尚未归档（daily-bars-tip-stock-universe 仍 in-progress），本 change 不修改任何已归档需求的语义。 -->

## Impact

- 修改：`src/cnequity/steps/bars.py`（`_finish_daily_bars` 返回结构化失败；`step_daily_bars` 透传；新增失败与 `failed_batches` 描述）、`src/cnequity/orchestrator/engine.py`（确认/补齐 worker step 返回 `status=failed` 时的批次与 run 记录透传）、`tests/unit/test_daily_bars_processing_granularity.py`（adapt 现有 raise 断言为状态断言 + 新增输出内容断言）。
- 不涉及：manifest 结构、`failed_scope_json`、audit findings、compact gate、retry 语义、CLI 签名（`cne run daily`/`cne backfill`/`cne retry` 均不变）。
- 副作用：失败不再打印 traceback（噪音下降）；只能从 run 结果/日志拿到结构化字段（`cne run daily` 简明输出仍不打印明细）。
- 部署注意：`pip install -e .` 生效；与 `daily-bars-tip-stock-universe` 的依赖关系——本 change 基于其已实现的 `failed_scope_json`/`unresolved_symbols` 字段（二者当前都已存在于工作树）。