## Context

`_finish_daily_bars`（`steps/bars.py`）在多日窗口 gap-fill 后仍有未解析缺口时 `raise RuntimeError("daily_bars: one or more symbol batches failed")`。该失败对系统是**已知、预期、可操作**的（断网重试 / 真缺口），但走的是通用异常通道：语义错位、结构缺失（符号/批次/缺口数只能挤进 message）、`logger.exception` 级联打印 traceback 制造"疑似崩溃"噪音。

数据安全并不依赖这个 raise：unresolved 批仍写为 manifest `failed` → `compact_allowed` 锁住 daily_bars → 水位线不推进；`cne retry` 按批状态（而非 run 状态）驱动。因此异常在这里的作用只剩"把 run 标 failed 并提醒运维"，而这些用 step 状态通道表达更合适，还能携带结构化载荷。本 change 基于 `daily-bars-tip-stock-universe` 已落地的 `failed_scope_json` 与 `_gapfill_multiday_via_kline` 的 `unresolved_symbols` 字段。

## Goals / Non-Goals

**Goals:**
- 预期缺口失败的报错从"抛 RuntimeError"改为"返回 `status=failed` + 结构化载荷"；
- 引擎日志从 traceback 降为"无背影、可操作"的一行，并用一条 `logger.error` 给出明细 + 重试指引；
- 结构化字段可被 run 结果 JSON / 脚本 / `cne status` 程序化消费；
- 保持严格语义与持久依据完全不变。

**Non-Goals:**
- 不改变 compact gate / manifest / retry 语义；
- 不改变 `cne run daily` / `cne backfill` / `cne retry` 的 CLI 签名或退出码语义；
- 不把 run 状态从 failed 降为 warning（缺口的告警级别仍为 failed）；
- 不做 retryability 自动分类（仅保留结构化字段供外部决策，自动台账另立 change）。

## Decisions

### D1. 预期缺口走 step 状态通道，异常只留真 bug

`_finish_daily_bars` 对"gap-fill 后仍 unresolved"返回：

```python
{
  "rows_read": n, "rows_written": n,
  "status": "failed",
  "unresolved_symbols": [...],        # 未解析的符号
  "missing_keys": m,                   # 符号×日期 缺口数
  "failed_batches": [{"batch_id", "symbol_count", "sample_symbols"}],
  "context_updates": {"audit_findings": [...]},   # 沿用现有 findings
}
```

- `step_daily_bars` 原样透传；`engine._run_step` 已原生支持 worker step 的 `status∈{success, warning, failed}`（engine.py:330-334），`merge_result` 见 `failed` → `had_error` → run `failed`。
- **异常只在真正的 bug/不变量违背时保留**：gap-fill 内部异常、schema 校验失败等照旧 raise。
- **依据**：`fetch_bars_via_sina`（收 failed 列表返回）、`derive_adj_factors`（failed_tasks + status）、`walk_day_backfill`（`status: warning`）已确立"业务结果走状态、异常走 bug"的惯例；`RuntimeError` 作 catch-all 让自动化无法按类型分流。
- **备选**：typed exception（`DailyBarsCoverageIncomplete`）—— 保留作为兜底，但状态通道信息更完整且无 traceback；一并采用会使双通道语义重叠，故本 change 选纯状态通道。

### D2. 输出内容与消费面（各表面看到什么）

- **run 结果 JSON**（引擎返回值 / `cne backfill` 全量输出）：step 条目含上述全部结构化字段。
- **引擎日志**：`Step daily_bars failed in 615.9s (0 rows)`（info，无 traceback）。
- **失败明细日志**（`_finish_daily_bars` 返回 failed 时 `logger.error` 一条）：
  `daily_bars incomplete over <start>..<end>: m symbol×date key(s) still missing (n symbol(s), e.g. a,b); failed batches: <batch_id>: k symbol(s); → run \`cne retry --run-id <id>\``
- **`cne run daily` 简明控制台**：维持 `{"run_id", "status"}` + exit 1（明细走错误日志/结果 JSON，可在后续另加 `--json` 全量呈现选项）。
- **持久层**：manifest `failed_scope_json` + `daily_bars_kline_gapfill` findings 不变（retry/审计的唯一依据）。

### D3. 复用既有字段，新增 `failed_batches` 描述

`unresolved_symbols`/`missing_keys` 直接来自 `_gapfill_multiday_via_kline` 已返回的 `unresolved_symbols`。`failed_batches` 由 `_describe_failed_daily_bar_batches(config, run_id)`（已在工作树中实现，读 manifest failed 批的 failed_scope/symbols_json）构造，随 status 一起返回，供日志与 run 结果使用。

### D4. 测试契约迁移

既有 `test_finish_daily_bars_error_names_failed_batches_and_symbols` 由"catch RuntimeError + 断言 message"改为"调用 `_finish_daily_bars` → 断言返回 `status=="failed"` 且 `unresolved_symbols`/`failed_batches`/`missing_keys` 内容正确"。新增真实 bug 仍 raise 的回归用例。

## Risks / Trade-offs

- **[状态通道依赖调用方检查 status]** → 唯一调用方 `step_daily_bars` 透传、引擎已检查；测试同步改为状态断言；外部直呼 `_finish_daily_bars` 需读 status（文档注明）。
- **[去掉 traceback 削弱崩溃定位]** → 真实 bug 仍 raise（traceback 保留）；预期失败由结构化 error 日志补偿。
- **[简明 CLI 不打印明细，运维可能漏看]** → 失败明细日志（ERROR 级别，`--quiet` 不吞）兜底；后续可选加 `cne run daily --json` 全量呈现。
- **[与 daily-bars-tip-stock-universe 的字段耦合]** → 该 change 已落地 `failed_scope_json`/`unresolved_symbols`；依赖其完成，实施前确认在工作树存在。

## Migration Plan

1. 改 `_finish_daily_bars`：未解析缺口返回 `status="failed"` + 结构化字段 + `logger.error`；保留真 bug raise。
2. 确认/补齐 `_run_step` 对 worker step `status="failed"` 的透传（含 run 结果中包含结构化字段）。
3. 迁移既有测试断言 + 新增输出内容/真 bug raise 用例；`ruff check` + `pytest tests/unit` 全绿。
4. CHANGELOG 记录（失败呈现代化 + 结构化字段）。
5. 回滚 = 恢复 `raise RuntimeError`（小改动，日志降噪与字段保留不影响回滚）。

## Open Questions

- 是否需要在 `cne run daily` 增加 `--json` 全量结果呈现（把 `results[step=daily_bars]` 的结构化字段直接打到控制台）——本期不做，视运维反馈再定。
- 是否进一步把"持久缺口"从"每轮 failed + retry"升级为"已知缺口台账/自动豁免观察"——另立 change。