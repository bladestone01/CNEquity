## Context

`fetch_trading_status_backup`（failover.py:376）在主源 EastMoney clist 失败时用 baostock `query_all_stock(day)` 快照站岗。`_fill_missing`（:341）对"快照缺失"的 SH/SZ A 股按前一日基线分类：

- 前一日非交易 → `fill_failure`（整体拒绝，不洗）；
- 前一日 normal 或**无记录** → 造 `normal` 行，计数进 `n_filled`；
- 阈值 `n_filled > max(50, universe//100)` → 整体拒绝。

问题集中在"**无记录 → 造 normal**"：从未观测过的符号（新上市未交易、名录外、板单漂移）被当成 `is_trading=True, status="normal"` 制造，违反"absence=ambiguity，绝不制造证据"的既有原则（baostock adapter 注释原话），并可能在 ≤ 阈值时漏进 curated，污染 ST/停牌覆盖。

## Goals / Non-Goals

**Goals:**
- "快照缺失 + 前一日无记录"的 A 股符号 → **当日缺行**（不制造 normal）；
- 提供 `n_missing_unseen` 计数（meta + audit finding），operator 可观测不污染数据；
- 保留"前日 normal 顺延"、"前日非交易拒绝"两分支与阈值/防洗/新鲜度守卫全部行为。

**Non-Goals:**
- 不新增任何状态词（不引入 `absent`，不放 `_NON_TRADABLE_STATUSES`）；
- 不改变 compact gate / watermark / schema / CLI；
- 不回填修正历史假 normal 行；
- 不自动解锁 daily_bars 的新股豁免（依赖 `list_date` 证据，另立 change）。

## Decisions

### D1. "无前记录缺失"→ 缺行（不发行）

`_fill_missing` 改为返回三类结果之一，替代当前的"normal 或 fill_failure"：

```
missing A 股符号:
  前日 ∈ {st,*st,suspended} → fill_failure（拒绝，不变）
  前日 == normal           → carry-forward 为 normal（不变）
  前日无记录               → **不发行**；记入 unseen
```

- 实现：`_fill_missing` 返回值扩展 `(fill_rows, fill_failures, n_filled, n_missing_unseen)`；`n_filled` 只数"顺延 normal"行（不再含无记录造行）。
- **依据**：诚实原则优先；且缺行是审计可见的真实缺口，比"假设正常"安全。与库存放原则一致（adapter 注释 + `_fetch_suspended_symbols` 对空全市场批的处理）。
- **备选（否决）**：发显式 `absent` 行——引入新词汇，豁免白名单/ST 收据/覆盖率统计/下一天 carry-forward 四处都要防误读，且容易被当成"已覆盖"或误判"非交易"；只为 ≤阈值边缘用例，得不偿失。

### D2. 观测计数 `n_missing_unseen`

`fetch_trading_status_backup` 的 meta 增加 `n_missing_unseen`（"缺失且无前记录"的 A 股符号数）。`step_trading_status` 消费 meta，在备份被接受时写入 audit finding：

```
{"check": "trading_status_backup_unseen_missing",
 "severity": "info",
 "message": "backup: N symbol(s) missing from snapshot with no prior record — left absent",
 "n_missing_unseen": N}
```

- 不发行、只计数：operator 在 retry JSON / audit 可见，但数据层保持诚实缺行。
- N=0 时不上 finding。

### D3. 既有守卫与门禁不变

- 阈值公式、`_baostock_has_day` 新鲜度、空快照拒绝、`fill_failures` 拒绝逻辑全部照旧（`n_filled` 的构成变小，阈值判定不变，只影响"造行"数量）。
- `trading_status` 为非 worker step、批 `blocks_compaction=False`，本就参与 compact gate 无关；本改动不触碰。
- 下游豁免/ST 收据读取的仍是真实状态或缺行，语义不变（只是不再有假 normal）。

## Risks / Trade-offs

- **[缺行使某些 day 覆盖密度下降]** → 这正是诚实信号；audit/lake_health 可定位；`n_missing_unseen` finding 提供定性归属。
- **[下一天 carry-forward 对无记录符号仍默认 normal]** → 无记录统一缺行后，"无记录"每次都诚实呈现缺行，不再通过造行污染；若该符号次日被快照收录则立即真实补齐。
- **[影响面 = 从未观测符号（新股/名录外），量级被阈值罩住]** → 本就是 ∩（缺失，无前记录）的小集合；蓝图无需回溯。
- **[对 daily_bars 新股豁免不自动解锁]** → 已知依赖 `list_date` 证据（instruments），属另一 change（`daily-bars` 系），本 change 不做。

## Migration Plan

1. `failover.py`：`_fill_missing` 返回 `n_missing_unseen`、不再造无记录 normal；`fetch_trading_status_backup` meta 透传。
2. `reference.py`：`step_trading_status` 读 meta，N>0 时写 audit finding。
3. 测试：新增/适配 `test_quality_failover.py`——(a) 无前记录缺失→缺行 + n_missing_unseen=N；(b) 前日 normal→顺延；(c) 前日非交易→拒绝继续成立；(d) N=0 无 finding。
4. `ruff` + `pytest tests/unit` 全绿。
5. CHANGELOG 记录。
6. 回滚 = 还原 `_fill_missing` 造 normal 分支（小改动）。

## Open Questions

- 是否需要对历史已入库的假 normal 做一次性清洗（按"日志日无快照记录且前日无记录"反查）——本期 Non-Goal，若覆盖污染确实影响判断再另立 change。