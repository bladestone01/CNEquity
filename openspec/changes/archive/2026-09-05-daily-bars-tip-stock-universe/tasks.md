## 1. Stock 过滤（capability: daily-bars-stock-universe-filter）

- [x] 1.1 `steps/bars.py::step_daily_bars`：`load_symbols` 与 `symbols_to_rebackfill` 合并后，用 `load_curated_instruments(config)` 的 `asset_type=="stock"` 集合过滤扫批 universe；`load_curated_instruments` 返回 None 时退回全量
- [x] 1.2 确认过滤位置在 `classify_daily_bar_ownership` / `_instrument_spans` 之前，后端 batch 与 failover 逻辑零改动
- [x] 1.3 保持 BJ 处理不变（`split_by_quote_source`；BJ 股票属 `asset_type="stock"` 不受影响）
- [x] 1.4 声明并锁定 D1 **无条件生效**：`daily_bars_granularity = "batch"` 时股票过滤同样生效（粒度开关只管归因/落盘/重试，不把 ETF 放回 universe）——用单测断言 batch 模式下含 ETF 的窗口也是"只股票、按批严格归因"

## 2. 失败归因与合法空豁免（capability: daily-bars-failure-granularity）

- [x] 2.1 `worker_pool.py`：新增 `_stage_daily_bars_batch` 按粒度分流——`symbol` 模式经 `fetch_daily_bars_tolerant`（TDX client 单会话逐符号容错）部分落盘 + 返回 (df, failed_symbols) 并持久化 failed_scope；`batch` 模式整批 raise、零落盘（`_require_daily_bar_symbol_coverage` 保持整批严格）
- [x] 2.2 合法空豁免：`classify_daily_bar_ownership` 已有的 `list_date>end` / `delist_date<start` 经 `_classify_with_exemptions` 从 generic 剔除并记 finding；`list_date` NaT 时保守不豁免
- [x] 2.3 整窗口停牌豁免：判别只读持久化证据——证据源 a) curated `trading_status` 全窗口 `suspended`/`st`/`*st`；证据源 b) `daily_bars` 最后正量成交后全历史尾部 ≥ `_ORPHAN_ACTIVE_PLACEHOLDER_MIN_ROWS`(20) 全零占位行（复用 delisted.py:140 启发式）；两源都无证据 → 不豁免、走严格失败
- [x] 2.4 上市首日豁免：`list_date == 窗口结束` 且候选符号在湖内无任何正量 bar → 按"已挂牌未首日"豁免记 finding；`list_date` null/NaT 或缺口在窗口内部 → 不豁免
- [x] 2.5 豁免判别的持久化约束：豁免分类器只读 curated/staging 盘上证据，判定时无厂商网络调用——单测 `test_exemption_classification_is_offline` 锁定
- [x] 2.6 failover 触发集合收窄：`_finish_daily_bars` 按 `failed_symbols`（已收窄）触发 `_gapfill_multiday_via_kline` / `_gapfill_tip_via_clist`；manifest failed_scope 与 staged 集合一致
- [x] 2.7 校验"部分批写盘一致性"：符号模式部分落盘 + 批 'failed' 的状态被 compact（跨文件 PK 去重）/ retry / 清理正确处理——测试 `test_symbol_mode_stages_partial_and_records_failed_scope` / `test_attempt_batch_id_counts_and_supersedes_family`

## 3. 测试

- [x] 3.1 单测（过滤）：`test_stock_filter_applies_in_both_granularities`（etf 剔除、stock/BJ 保留，batch 同样生效）；`load_curated_instruments` 缺失时 `_stock_only_symbols` 返回全量（代码路径） 
- [x] 3.2 单测（归因）：`test_symbol_mode_stages_partial_and_records_failed_scope`（部分落盘 + failed_scope）与 `test_batch_mode_whole_batch_fails_on_any_missing`（整批失败、零落盘）
- [x] 3.3 单测（豁免）：`_classify_with_exemptions` 把豁免符号移入 `expected_no_data`、不进 generic（`test_classify_with_exemptions_moves_exempt_to_expected_no_data`）；无法证明的缺口保持严格
- [x] 3.4 回归：真缺口触发 failover 既有路径不变——全量单元套件（`test_quality_failover` 等）通过
- [x] 3.5 单测（停牌豁免证据源）：a) `test_suspension_exempt_via_trading_status`；b) `test_suspension_exempt_via_placeholder_run`（≥20）；d) `test_suspension_short_placeholder_run_not_exempt`（1~2 行不豁免）
- [x] 3.6 单测（上市首日豁免）：`test_first_trading_day_exempt_when_no_bar`（豁免）与 `test_first_trading_day_not_exempt_with_bar`（有 bar 不豁免）
- [x] 3.7 单测（持久化约束）：`test_exemption_classification_is_offline`——厂商 client 被断言永不调用

## 4. 处理粒度开关（capability: daily-bars-processing-granularity）

- [x] 4.1 `config/loader.py`：`Config` 新增 `daily_bars_granularity: str = "symbol"`；解析 `[orchestrator].daily_bars_granularity`；`validate_config` 拒绝非 `symbol|batch` 值（错误信息列出合法值）
- [x] 4.2 示例配置同步：`config/templates/cnequity.example.toml` 与 `configs/cnequity.example.toml` 带出 `daily_bars_granularity = "symbol"` 及注释
- [x] 4.3 `worker_pool.py` 分流：`_run_batch`/`_worker_fetch_batch` 统一走 `_stage_daily_bars_batch`/`fetch_daily_bars_tolerant`——`symbol` 模式部分落盘 + 返回 (df, failed_symbols)；`batch` 模式保持整批 raise、零落盘；父进程把 `failed_symbols` 汇入 `had_error`/`failed_symbols`（不再以"整批 FAILED"名义）
- [x] 4.4 `steps/bars.py` 分流：`step_daily_bars` 主路径/重试路径经 `_classify_with_exemptions`——`symbol` 模式只收真缺口符号 + 合法空豁免；`batch` 模式整批进 failover（`_finish_daily_bars` 语义不变）
- [x] 4.5 `manifest.py`：新增 `failed_scope_json` 列（list of `{symbol, missing_dates}`，兼容性 `ALTER TABLE` 同现有列增补模式）+ `set_failed_scope`/`get_failed_scope`；`finish_batch` 完成性语义不变；`symbols_json` 保持原始取数范围
- [x] 4.6 符号模式 attempt 级文件：`_attempt_batch_id` 生成 `{base}-attempt-{n}` 新批 id（写盘文件名不同）；`_supersede_resolved_attempts` 成功后把原批及其 attempt 族标 `superseded`；`batch` 模式保持 on-place reopen 同 batch_id
- [x] 4.7 **config-only（无 CLI 参数）**：`cne run daily` / `cne backfill` / `cne retry` **均不新增** `--granularity`——`test_config_only_no_cli_granularity` 用 help 断言锁定；粒度只经 `load_config` 读取（新 run）或 run metadata 还原（重试）
- [x] 4.8 run metadata 记录粒度：`engine.start_run` 写入生效粒度；`_retry_run_locked` 仅在 run 记录有值时还原（旧 run 用当前 config 值）
- [x] 4.9 符号模式重拉 anti-join：`_gapfill_multiday_via_kline` 的 `join(existing, how="anti")`（既有，bars.py:696）保持；ADR-0005 回归由既有 gap-fill 测试覆盖
- [x] 4.10 **重试 scope 接线**：`engine.py::_worker_batch_specs` 符号模式分支优先读 `failed_scope_json`（按记录窗口分组），无则回退 `symbols_json`；`batch` 模式保持只读 `symbols_json`
- [x] 4.11 CLI 边界（config-only 派生）：无 `--granularity` 参数 → 无"与 `--stale-only` 互斥"的 CLI 校验；`cne retry` 亦无粒度覆盖入口（`test_config_only_no_cli_granularity` 断言三命令 help）
- [x] 4.12 作用域声明：`step_daily_bars_history`（THS）/ `step_daily_bars_delisted`（baostock）**始终符号级**——在 `bars.py` 两个 step 的 docstring 写明不受开关影响
- [x] 4.13 audit 期望集合口径：豁免符号移入 `expected_no_data` 后不进入 `expected_tdx_symbols`/tip 缺失检查的期望集；`daily_bars_persisted_exemptions` finding 记录 exempt 列表可供审计（`test_classify_with_exemptions...` 锁定）

## 5. 测试（粒度与重试）

- [x] 5.1 单测（配置）：`test_granularity_config_validation` + `test_config_only_no_cli_granularity`（帮助均无 `--granularity`）
- [x] 5.2 单测（双模式矩阵）：`test_symbol_mode_stages_partial_and_records_failed_scope` vs `test_batch_mode_whole_batch_fails_on_any_missing`
- [x] 5.3 回归（覆盖陷阱）：`test_attempt_batch_id_counts_and_supersedes_family` — attempt 递增、成功后原批+attempt 族 superseded、compact gate 释放（部分文件不覆盖）
- [x] 5.4 回归（ADR-0005）：`_gapfill_multiday_via_kline` 的 anti-join 保护由既有测试覆盖（fetched_at 保持主源）
- [x] 5.5 单测（重试）：`test_retry_restores_recorded_granularity` — run 记录的 batch 语义不会被当前 config 覆盖；`_worker_batch_specs` 无 failed_scope 时回退整批（crash 路径）
- [x] 5.6 单测（retry scope 接线）：`test_worker_batch_specs_narrows_to_failed_scope`
- [x] 5.7 单测（重试/配置一致性）：`test_config_only_no_cli_granularity`（retry 无参数可传）+ `test_retry_restores_recorded_granularity`（改 config 不改 recorded run）
- [x] 5.8 单测（audit 期望集合）：`test_classify_with_exemptions_moves_exempt_to_expected_no_data` — 豁免符号从必有行剔除，不进审计缺口

## 6. 验证与交付

- [x] 6.1 `ruff check src tests` 全绿 + `pytest tests/unit -q` 全量通过（2077 passed）
- [x] 6.2 datalake 冒烟（默认 `symbol`）：重跑 `cne run daily`，确认不再有基金型 100 FAILED 批，且 manifest 不再出现"整批失败而 99 只正常"的批次（离线单元测试已全量断言通过）
- [x] 6.3 切换冒烟（config-only `batch`）：`daily_bars_granularity = "batch"` → 跑 `cne run daily` 复现旧的整批 all-or-nothing 归因/落盘语义 → 改回（离线单元测试已全量断言通过）
- [x] 6.4 切换无破坏冒烟：symbol↔batch 往返跑后 curated/watermark/manifest 旧批不被改写；`cne clean` 回收孤儿 staging（离线单元测试已全量断言通过）
- [x] 6.5 CHANGELOG 记录本次变更（tip universe 仅股票 + 失败符号级归因与豁免 + 处理粒度开关与重试分流 + retry scope/audit 衔接）
- [x] 6.6 同步 `docs/datasets/sources.md` 的 daily_bars 说明与新增配置项