## Context

`cne retry --run-id` 走 `engine.run_job("retry", retry_failed_only=True, run_id=...)`，`run_job` 在 retry 分支前把 `trade_date` 默认成 `shanghai_today()`（engine.py:83）并直接转给 `_retry_run(run_id, trade_date)`（engine.py:87）。于是 `_run_step(name, trade_date=今天, ...)` 把日期锚定的非 worker step（`trading_status` 等）重跑成"今天"。

而 run 启动时已把 `trade_date` 写进 run metadata（engine.py:110 `metadata = {"trade_date": ..., "backfill": ...}`），`_retry_run_locked` 也读了 `run_meta`（用于还原 `_backfill`/`_backfill_scope`），只是没把 `trade_date` 用回来——这是可一行修正的一致性缺口。

worker 批 step（`daily_bars`）不受影响：`_worker_batch_specs` 从 manifest 批次 `window_start/end` 取窗口，重试天然重跑原窗口；只有按「交易日」取数的非 worker step 才依赖传入的 `trade_date`。

## Goals / Non-Goals

**Goals:**
- `cne retry --run-id` 对日期锚定 step 的重跑沿用 run 记录的 `trade_date`；
- 修复实测场景：`--trade-date 2026-08-21` 的 run 重试不再把 trading_status 跑成 08-22；
- 旧 run（无 trade_date 记录）与 worker 批行为零变化。

**Non-Goals:**
- 不给 `cne retry` 增加 `--trade-date` 显式覆盖（避免与 run 记录值语义重叠；留作后续扩展）；
- 不改 manifest schema、CLI 签名、worker 批窗口逻辑、staging/compact/gate 语义。

## Decisions

### D1. retry 的 trade_date 解析优先级：run 记录 > 传入/今天

在 `_retry_run` 入口解析最终锚点：

```python
def _retry_run(self, run_id, trade_date, *, auto_finalize=True):
    run_meta = self.manifest.get_run_metadata(run_id)
    recorded = run_meta.get("trade_date")
    if recorded:
        try:
            trade_date = date.fromisoformat(recorded)
        except (TypeError, ValueError):
            pass  # 损坏/非法日期 → 保留传入值（today），防 retry 崩溃
    return self._retry_run_locked(run_id, trade_date, auto_finalize=auto_finalize)
```

- `_retry_run_locked` 已读 run_meta 还原 `_backfill`/`_backfill_scope`，此处额外还原 `trade_date` 与之并列，一致且一次读。
- **依据**：`cne retry` 的契约是"重放原 run 的失败范围"；日期锚点是 run 的一部分，应来自 run 元数据而非进程当天的时钟。
- **备选**：在 CLI 层解析 run_meta 并传参 —— 否决，`engine.run_job` 是唯一入口，集中处理更内聚。

### D2. 上下文与 step 的透传

`_merge_retry_context` 用 `if key not in context` 填充 run_meta（engine.py:423-428），`context["trade_date"]` 已由参数设置，不会被 run_meta 覆盖——因此只要 `_run_step(..., trade_date=记录值, ...)` 传的是 D1 解析后的值，`context["trade_date"]` 与 step 收到的 `trade_date` 自然一致，日期锚定 step 即重跑原日期。worker 批不受影响（批次窗口来自 manifest `BatchSpec`）。

### D3. 边界与兼容

- **run 无 `trade_date` 记录**（旧 run/直接 `manifest.start_run` 构造）：`recorded` 为空 → 回退传入/`shanghai_today()`，行为与现状一致。
- **非法记录值**：`fromisoformat` 抛错 → 保留传入值，不因元数据脏数据导致 retry 全线失败。
- **retry 不经过 trading-day 守卫**（`run_job` 对 retry 提前 return），沿用交易日/非交易日都与原 run 一致；`_backfill`/backfill_scope 还原逻辑不变。
- **init/resume 路径**：`resume_init` 调 `_retry_run`，init run 同样记录了 trade_date，沿用即可；语义上 init 以窗口为主，trade_date 影响小，但保持一致无害。

## Risks / Trade-offs

- **[run_meta 无 trade_date 时行为不变]** → D3 已覆盖；单测锁定回退路径。
- **[沿用旧 trade_date，运维想"补今天"时易困惑]** → 文档注明 "`cne retry` = 重放原 run 的日期锚点"；`--trade-date` 显式覆盖列为 Not-Goal / 后续扩展。
- **[元数据污染导致解析异常]** → D1 捕获并回退 today，retry 不崩。

## Migration Plan

1. `engine.py`：在 `_retry_run` 解析 `run_meta["trade_date"]`（D1），透传不变。
2. 测试：新增/适配 `test_worker_manifest` 与 engine 测试——(a) run 记录 08-21 且当前 08-22 时 retry 用 08-21；(b) 无记录回退今天；(c) 非法日期回退今天；(d) worker 批窗口不受影响。
3. `ruff` + `pytest tests/unit` 全绿。
4. CHANGELOG 记录。
5. 回滚 = 撤 `_retry_run` 的 trade_date 解析一行。

## Open Questions

- 是否需要 `cne retry --trade-date` 显式覆盖（在"重放原 run"之外提供"补到特定日"）——本期 Non-Goal，视运维需要另立 change。