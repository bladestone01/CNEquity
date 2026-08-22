## 1. 已覆盖短路 + 时段守卫（capability: trading-status-run-guards）

- [x] 1.1 `reference.py` 新增 `_TRADING_STATUS_FINAL_AT = time(16, 0)`（导入 `time`/`datetime`/`shanghai_now`/`is_trading_day`）
- [x] 1.2 新增 `_trading_status_covered(config, trade_date, expected_symbols) -> bool`：curated 分区分日扫描 + `expected ⊆ observed` 严格全量
- [x] 1.3 新增 `_trading_status_window_eligible(config, trade_date, *, now=None) -> bool`：`now>=16:00` / `trade_date < today` / 非交易日 → eligible
- [x] 1.4 `step_trading_status` 编排序：backfill → covered（`already_completed` info 提示 + return）→ eligible（否则 `before_cutoff` warning + return）→ 正常抓取

## 2. 真实原因浮现

- [x] 2.1 `_fetch` except：`backup is None/empty` 时 `raise RuntimeError("... primary(eastmoney) failed; backup declined: <reason>") from primary_exc`
- [x] 2.2 `failover.py` 各拒绝分支补 `logger.warning`（含 reason），不改变返回结构
- [x] 2.3 `check` 词汇表统一：`already_completed` / `before_cutoff` / 失败路径带 reason 文本

## 3. 测试

- [x] 3.1 单测：covered 短路——monkeypatch `fetch_trading_status` 为必炸，断言不触发、返回 info finding `already_completed`
- [x] 3.2 单测：部分覆盖不短路（observed ⊂ expected → 继续抓取）
- [x] 3.3 单测：`before_cutoff`——注入 `now`（09:15，当前交易日）→ 跳过 + warning + 提示含"16:00"
- [x] 3.4 单测：16:00 整 / 历史日 / 非交易日 → eligible 照常抓取（注入 `now`/`trade_date`）
- [x] 3.5 单测：backup 拒绝时 raise 文本含 reason（stale / 未配置）
- [x] 3.6 `uv run ruff check src tests && uv run pytest tests/unit -q` 全量通过

## 4. 验证与交付

- [x] 4.1 datalake 冒烟两时态：09:00 跑 `cne run daily --group core` → `before_cutoff` warning + 提示语；17:00 跑 → 正常主/备路径
- [x] 4.2 CHANGELOG 记录（可观测性/守卫）
- [x] 4.3 同步 `docs/operations/runbook.md` / README trading_status 说明（读取时段语义）
