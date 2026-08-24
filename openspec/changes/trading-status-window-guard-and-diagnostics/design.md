## Context

`trading_status` 主路径（reference.py `step_trading_status`）：东财主源 → 失败时 `fetch_trading_status_backup`（baostock）→ 备份拒绝则 `raise primary_exc`。2026-08-20 09:14 早间运行实证：主源被东财 IP 桶挡、备份被新鲜度闸拒绝（`baostock has no data for D yet`），最终只显示东财 clist 报错，真实原因被吞。同时段"已补齐"的 rerun 是静默 0 行。bars 侧已有同类时间守卫先例（`_reject_unfinished_daily_bar_window`：`shanghai_now` + `is_trading_day` + 15:00 cutoff）。

## Goals / Non-Goals

**Goals:**
- 交易时段（<16:00，当前交易日、未覆盖）明确跳过并给出可执行提示。
- rerun 已补齐时明确提示，避免误导性 ERROR / 静默 0 行。
- 失败/跳过全程可归因：日志 + finding 带统一 `check` 词（`already_completed` / `before_cutoff` / backup 拒绝原因）。

**Non-Goals:**
- 不改数据抓取逻辑/路由本身（仍主源东财 → baostock 兜底）。
- 不强制"禁止早间跑"——只是把模糊 ERROR 变成可解释 SKIP。
- 不改 schema、写路径、manifest 结构。
- 不处理 daily_bars（属另一 change：`daily-bars-tip-stock-universe`）。

## Decisions

### D1. 覆盖判定用"严格全量超集"

`_trading_status_covered(config, D, expected)`：`dataset_has_parquet(curated/trading_status)` 且扫描 `trade_date==D` 的 `symbol` 集合满足 `expected ⊆ observed`（全量、非空）。**只认齐全才短路**，局部覆盖必须继续抓取（防止误判跳过造缺口）。

- partition 剪枝 + `unique()` 扫描，成本可忽略；复用 `query/parquet_scan` 工具。

### D2. 时段守卫语义 = "跳过 + warning"，沿用仓库时间基

`_trading_status_window_eligible(config, D, now=None)` 镜像 bars 守卫：`shanghai_now(now)`、`now>=16:00` 或 `D<today` 或 `非交易日` → eligible；否则 `before_cutoff` 跳过。

- **状态语义采用 A**：`status="warning"` + `check="before_cutoff"` finding——audit 诚实显示"今日未采（时段外）"，不侥幸当成功；代价是 dashboard 会出现单条 warning（可接受，属显式信号而非噪声）。
- 备选 B（`status="success"` + info finding）记录在案，若监控阈值敏感再切换（仅改返回一处）。
- `_backfill` 标志在步骤入口已提前 return，守卫天然不干扰历史回填。

### D3. 失败/跳过统一可归因

- `_fetch` 的 except：`backup is None` 时 `raise RuntimeError("trading_status: primary(eastmoney) failed; backup declined: <degraded.reason>") from primary_exc`——原因来自协调器 meta（已全量带 reason）。
- 协调器各拒绝分支补 `logger.warning`（信息量不变，只增强日志可见性）。
- `check` 词汇表统一：`already_completed` / `before_cutoff` / 失败路径自然带 reason 文本。

## Risks / Trade-offs

- **覆盖判定误判** → D1 严格全量超集 + 非空，误判仅可能"漏判"（部分覆盖仍走抓取），不会"错判"（局部被当齐全跳过）。
- **warning 状态噪声** → 仅在"当前交易日 & 16:00 前 & 未覆盖"出现，窗口窄；D2 备选 B 留退路。
- **时区/边界** → 统一 `shanghai_now`（与 bars 一致），16:00 整点 `>=` 判定；测试注入 `now` 覆盖边界。
- **早间抓当日仍可能偶发失败（东财恢复+baostock 未结算）** → 守卫跳过后晚间重跑自然补齐（watermark 未推进），语义一致。

## Migration Plan

1. 实现三守卫 + step 编排 + 异常组合 + 单测（离线，注入 `now`）。
2. `uv run ruff check src tests && uv run pytest tests/unit -q` 全绿。
3. datalake 冒烟两个时态：09:00 跑 → `before_cutoff` warning + 提示语；17:00 跑 → 正常主/备路径。
4. 回滚：去掉守卫短期短路与异常组合即恢复现状（无配置/数据残留）。

## Open Questions

- warning 状态在 dashboard/告警中的容忍度：若阈值敏感，切 D2 备选 B。
- `before_cutoff` 工具化：是否需要 `cne run daily --force` 之类显式放行开关（当前不做，观察需求）。