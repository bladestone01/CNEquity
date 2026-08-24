## Why

`fetch_trading_status_backup` 的 `_fill_missing`（`quality/failover.py:341`）在 baostock `query_all_stock(day)` 快照**缺失**某个 SH/SZ A 股时，只要该符号**前一日无记录**（从未观测过），就**编造一行 `is_trading=True, status="normal"`**。这是把"不知道"当成"正常"的正面证据制造：新上市未交易/名录外符号会被错标为 normal；且规模小（≤阈值）时会漏过门槛进入 curated，污染 ST/停牌覆盖的诚实性。多次实测（新股 603448.SH 等）印证该分支与"上市未交易"语义相悖。仓库既有原则（adapter 注释："absence=ambiguity，绝不制造证据"；空全市场批不得当"无停牌"）要求：**无观测即缺行**。

## What Changes

- **`_fill_missing` 去掉"无前记录→造 normal"分支**：A 股符号在 baostock 快照缺失时，
  - 前一日 curated 是 `st/*st/suspended` → 维持既有 `fill_failure`（整体拒绝，绝不洗）；
  - 前一日是 `normal` → 维持既有"顺延 normal"（carry-forward，合理）；
  - **前一日无记录（从未观测）→ 不制造 `normal` 行**，该符号当日**缺行**（不发行），保持"无观测=无数据"。
- **新增 step meta 计数 `n_missing_unseen`**：统计"缺失且无前记录"的 A 股符号数，随 `fetch_trading_status_backup` 的 meta / audit finding 输出，operator 可观不可污染数据（不用新状态词）。
- **不影响门禁**：不新增状态词、不触碰 `_NON_TRADABLE_STATUSES`、不改变阈值/防洗/新鲜度守卫；trading_status 的 staging→curated 依旧只由"step 成功产出 + compact"决定，与本改动无关。

## Capabilities

### New Capabilities

- `trading-status-backup-absent-not-fabricated`: trading_status 备源对"快照缺失且无前记录"的 A 股符号改为**缺行（不编造 normal）+ step meta 计数**，保留"前日 normal 顺延 / 前日非交易拒绝"两分支与全部既有守卫不变。

### Modified Capabilities

<!-- 无既有已归档 spec 受影响。 -->

## Impact

- 修改：`src/cnequity/quality/failover.py`（`_fill_missing` 返回值/语义 + `fetch_trading_status_backup` meta 增加 `n_missing_unseen`）、`src/cnequity/steps/reference.py`（`step_trading_status` 消费 meta 并写入 audit finding）、`tests/unit/test_quality_failover.py` 等。
- 不涉及：`_NON_TRADABLE_STATUSES`、阈值 `max(50, universe//100)`、新鲜度/空拒/防洗守卫、compact gate、schema、CLI。
- 副作用：已入库的**假 normal** 不会回填修正（本 change 只改未来行为）；覆盖缺口向 audit/lake_health 可见（诚实缺口）；对 daily_bars 新股豁免不自动解锁（仍需 `list_date` 证据，属另一 change）。
- 部署注意：editable 安装即时生效；无配置变更。