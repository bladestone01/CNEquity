## Why

`cne retry --run-id <id>` 在 `engine.run_job` 里把 `trade_date` 默认成 `shanghai_today()`（engine.py:83）并直接传给 `_retry_run`。worker 批 step（如 `daily_bars`）重试时用的是 manifest 批次的 `window_start/end`，所以不受影响；但**按日期锚定的非 worker step（如 `trading_status`）会被重跑成"今天"**。

实测：第一次 `cne run daily --trade-date 2026-08-21` → `cne retry` 把 `trading_status` 重跑成 2026-08-22（周六、baostock 未结算）→ 主源 EastMoney clist 全 host 失败 + 备源 baostock "not settled" 拒绝 → `TdxSourceError`，一个无关紧要的失败反复出现。更糟的隐患：一旦 EastMoney 恢复，重试会把 trading_status 写成 **08-22 的行**，而原 run 真正缺的 **08-21** 反而无人补——"重试成功但补错了日期"。

run 启动时本就把 `trade_date` 写入 run metadata（engine.py:110），retry 却没读它，属于可以一行修掉的一致性问题。

## What Changes

- **retry 的 trade_date 锚点改为"run 记录优先"**：`_retry_run(_locked)` 解析重试 trade_date 时，优先使用 `run_meta["trade_date"]`（存在时），否则回退到调用方传入/`shanghai_today()`。这样 `cne retry` 真正"重放原 run"，日期锚定 step（trading_status 等）重跑原日期，不再漂移到今天。
- 仅作用于**记录了 trade_date 的 run**（`run_job` 启动的 daily/backfill/init 系列都记录）；旧 run 缺失该字段时行为不变（回退 today）。
- worker 批 step（`daily_bars`）行为不变（批次窗口仍来自 manifest），无需改动。
- **非目标**：不给 `cne retry` 增加 `--trade-date`（本期不做，避免覆盖语义与 run 记录值重叠）；不改变 CLI 签名。

## Capabilities

### New Capabilities

- `retry-run-date-anchoring`: `cne retry --run-id` 对按日期锚定的 step 重跑时沿用 run 记录的 `trade_date`（优先于 `shanghai_today()`），避免重试漂移到"今天"并防止"补错日期"。

### Modified Capabilities

<!-- 无既有已归档 spec 受影响。 -->

## Impact

- 修改：`src/cnequity/orchestrator/engine.py`（`run_job`/`_retry_run`/`_retry_run_locked` 的 trade_date 解析与 `_merge_retry_context` 透传）、`tests/unit/test_worker_manifest.py` 等（retry 相关测试适配/新增）。
- 不涉及：manifest schema、CLI 签名、worker 批窗口逻辑、staging/compact/gate 语义。
- 副作用：`cne retry` 对已记录 trade_date 的 run 不再锚定今天——与"重放原 run"的目标一致；对旧 run（无该字段）零影响。
- 部署注意：editable 安装即时生效（datalake venv 为 editable）；无需配置变更。