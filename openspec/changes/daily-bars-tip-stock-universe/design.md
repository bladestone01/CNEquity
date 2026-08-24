## Context

`daily_bars` 日更 tip 全市场扫批的 universe 来自 `load_symbols(config)`（实测 7420 只），其中含 ~600 只场内基金/ETF/REIT（`asset_type="etf"`）。`_require_daily_bar_symbol_coverage`（worker_pool.py:84）对批次**逐股严格覆盖**：这些标的在增量窗口内无 TDX 日线 → 整批 RuntimeError → 该批 100 只全标 FAILED。manifest 实测 8 个失败批全部由此触发，且完全确定性复现。

`step_daily_bars_history`（bars.py:1105）已用 `load_curated_instruments` 过滤 `asset_type=="stock"`；tip 侧缺同样过滤，两侧口径不一致。

## Goals / Non-Goals

**Goals:**
- tip 全市场扫批 universe 与 history 一致：仅 `asset_type=="stock"` 的股票。
- 消除"基金/ETF/REIT 无日线 → 整批殉葬"这一确定性失败。
- 保持股票侧的严格覆盖门槛与既有 failover（东财 clist 快照）不变。

**Non-Goals:**
- 不改变 `_require_daily_bar_symbol_coverage` 语义（对股票仍严格）。
- 不做"基金/ETF 日线"数据集（属另设数据集）。
- 不把筛选做成配置开关（与 history 硬编码口径保持天然一致）。

## Decisions

### D1. 在 `step_daily_bars` 入口复用 `load_curated_instruments` 的 stock 过滤

`load_symbols(config)` 之后、`classify_daily_bar_ownership` 之前，用 `load_curated_instruments(config)` 取 `asset_type=="stock"` 的 symbol 集合做交集；`load_curated_instruments` 返回 None（无 instruments 数据）时退回全量（与 history 同款容错）。

- **依据**：与 history 使用同一个数据源/判定，避免两处规则漂移；早于 ownership 分级，后端的 batch/failover 逻辑零改动。
- **备选**：在 worker_pool 或覆盖门槛内按需豁免——否决：把"universe 选型"混进"覆盖率校验"职责，且仍会为基金白白发请求。
- **rebackfill / `symbols_to_rebackfill`**：同样经过该过滤（历史重拉只针对股票）。

### D2. 残留场景改由符号级归因与豁免承接（不再"整批交给 failover"）

过滤之后仍有股票残留触发空行（新股窗口内无日线、整窗口停牌、退市边界）。为避免"一只毒倒一百只"，`_run_batch` 的失败语义从批级改为符号级：

- `fetch_daily_bars` 已在逐股循环，把"无行"**记到符号**（`failed_symbols`），正常股票照常进入 `writer.write_batch` 落盘——不再在 staging 前整体抛错（worker_pool.py:84 门槛移到按符号判定）。
- tip / 多日 failover 的**触发集合**从 `set(failed_symbols)` 收窄为"真缺口"符号（bars.py `_gapfill_multiday_via_kline(failed_set)`、`_gapfill_tip_via_clist` 本就按 failed_set 逐符号补——现在 failed_set 不再是整批）。
- **依据**：原门槛只为防"静默缺口跳过 failover"（worker_pool.py:85-91 注释）；符号级判定保留了同样保障，爆炸半径从 100 收敛到 1。

### D3. "合法空"豁免：未上市 / 已退市 / 整窗口停牌

判定"本就没有可抓数据"的符号，先于失败判定豁免：

- `list_date > 窗口结束`（尚未上市）→ 豁免（记 finding，counted）；
- `delist_date < 窗口开始`（已退市）→ 豁免；
- 整窗口无可交易日 bar 且被判定为"全窗口停牌"（参照 `trading_status` 或 bar 完全缺失）→ 豁免；
- 只有不满足任何豁免条件的空行才判失败并进 failover。

豁免依据读取 `load_curated_instruments` 的 `list_date/delist_date`；当前快照部分 `list_date` 为 NaT 时退化为"无法证明未上市 → 不豁免"（保守，宁抓不改）。

**豁免判别的证据源（只读持久化）**——按 spec `daily-bars-processing-granularity` 的 constraint，整窗口停牌豁免的两个证据源均为盘上数据，禁止在判定时直连实时接口：

1. **`trading_status` 数据集**：curated `trading_status` 中整个窗口内该符号为 `suspended`（或 `st`/`*st`）；
2. **`_ORPHAN_ACTIVE_PLACEHOLDER_MIN_ROWS` 占位串启发式**（delisted.py:140，阈值=20）：窗口内最后一个正量成交之后连续 ≥20 个全零占位行（`open==high==low==close && volume=0`）——这条"尾部占位串＝供应商还在跟踪＝停牌而非退市"的判别，与 `repair_delisted_instruments`（delisted.py:1167-1192）同源，不另造。

**"上市首日"豁免**（`list_date == 窗口结束`，即已挂牌但首个实际成交日尚无行情）同样只读持久化 `list_date` + `daily_bars` 无行证据；`list_date` 为 null/NaT 或缺口在窗口内部时不豁免。list_date 的"挂牌日 vs 首个交易日"语义权威性仍是主要风险（见 Open Questions）。

**硬约束**：所有豁免分类器（未上市/已退市/整窗口停牌/上市首日）只允许读持久化证据。理由：豁免专为"源不可用/波动"场景而设——判定依赖实时接口恰在该接口最不可信的时刻，且非确定性分类会让 manifest failed-scope、`cne retry`、compact gate 跨 run 不稳定。

### D4. 处理粒度开关：`[orchestrator] daily_bars_granularity = symbol | batch`

单一 API/抓取路径上同时存在两套语义，改成配置开关而不是硬切到符号级：

- **`symbol`（默认）**：新行为——部分成功先落盘、失败归因到符号×日期、failover/retry 精确重拉（本 change D2/D3 的完整形态）。
- **`batch`（legacy）**：精确复刻现状——`_run_batch` 遇到任何符号异常/覆盖缺口整体 raise、整批废弃、整批进 failover、retry 整批重拉；`_require_daily_bar_symbol_coverage` 保持整批 raise。用于需要严格全有或全无语义的环境，或作为线上回滚的一刀切开关。

承载：
- `Config` 新增字段 `daily_bars_granularity: str = "symbol"`（`config/loader.py`），解析 `[orchestrator].daily_bars_granularity`，`validate_config` 拒绝非 `symbol|batch` 值；随 `templates/cnequity.example.toml` 与 `configs/cnequity.example.toml` 同步出默认值。
- 分流点：`worker_pool.py::_run_batch/_worker_fetch_batch`（抓取/落盘/抛错语义）、`steps/bars.py::step_daily_bars/_finish_daily_bars`（gap-fill 触发集合、tip 端局）、`engine.py::_retry_run`（重试 scope 的读取与还原）。
- **配置文件是唯一入口，无 CLI 参数**。`cne run daily`、`cne backfill` **均不提供** `--granularity`（与仓库"策略入配置、一次性范围入 CLI"的分寸一致：granularity 是处理策略，不是"本次抓什么"的抓取范围，故不进 `_override_scope`/`--symbols` 那类 CLI 覆盖）。粒度只随每次 `load_config` 读取；`--stale-only` 无独立分支，直接用配置值；`cne retry` 无粒度可传，永远用 run 记录的粒度。
- **重试确定性**：run_id 启动时把生效粒度写入 run metadata（沿用 `engine._retry_run` 从 metadata 还原 `_backfill_start/end` 的既有模式，engine.py:496-507）。`cne retry --run-id` 先还原该 run 的粒度再决定 scope 语义，避免"用当前配置重试历史 run"时对 `symbols_json` 口径的误解。
- **临时实验的操作纪律**：config-only 的代价是"一次性实验"须改共享配置文件、跑后改回——否则被 cron/launchd 复用的配置会带着实验状态进入后续定时运行。缓解：run metadata 已记录每个 run 的实际粒度，`cne status`/`cne retry` 可审计；文档明示"临时切换粒度 = 改 config → 跑 → 改回"。
- **依据**：`batch` 模式保住既有管控（严格覆盖门槛就是 `_require_daily_bar_symbol_coverage` 的本意，worker_pool.py:85-91）；开关让两条语义共享同一代码路径，靠分支而非复制。
- **备选**：① 保留 `--granularity` CLI 逃生口——否决：引出与 `--stale-only`/`--group`/`--backfill` 的互斥与校验面，且与"策略入配置"惯例相悖（详见探索结论）；② 永远符号级、不留 batch 模式——否决：ETF/严格环境失去精确回退，且"整批原子性是特性还是缺陷"尚无定论，留开关观望更稳。

### D5. 符号模式的硬规则：failed-scope 持久化 + attempt 级文件 + anti-join 写盘

符号级部分落盘会引入两种数据风险，各配一条硬规则：

1. **retry scope 必须精确到符号×日期并持久化**。`symbols_json` 收窄会丢失"该批原始 100 只"的审计信息，新增 manifest 列 `failed_scope_json`（list of `{symbol, missing_dates}`）承载缺口；`symbols_json` 保持原始取数范围不变。`set_batch_symbols`（无 status 守卫）只用于非 worker 步的既有收窄路径，不在此复用。重试只重拉缺口键。
   - 例外：**crash 路径不窄化**。worker 被 OOM/kill（`BrokenProcessPool`）时 `finish_batch` 未执行，批走 running→stale→failed，`failed_scope_json` 为空 → 重试退化为全量 scope（安全，只浪费）。
2. **attempt 级文件命名，禁止复用确定性 batch_id 覆盖部分文件**。`part-{batch_id}.parquet` 由确定性 `{start}_{end}-batch-{i}` 推出（storage/parquet.py:28）。若重试复用同一 batch_id 写盘，会把首次 99 只部分批次文件整体覆盖、compact 后 curated 丢 99 只。符号模式重试一律用 `{batch_id}-attempt-{n}` 新批次 id 写新文件；compact 跨文件按 `fetched_at sort keep=last` 去重（storage/parquet.py:118），天然容忍多个 attempt 文件共存。
3. **重拉结果写盘前 anti-join**。复用 `_gapfill_multiday_via_kline` 的既有 protect（bars.py:696 `join(existing, how="anti")`），把"精确查询"的粒度收窄到缺失的符号×日期，绝不覆盖已由主源（TDX）覆盖的键——这是 ADR-0005"备份不能自动覆盖主源行"在 fetched_at keep=last 合并下的硬约束，不是效率优化。

### D6. rerun / 重试随粒度分流

`engine._retry_run` 已只挑 failed/warning 批（engine.py:513），按其还原的粒度选择 scope 语义：

- **batch 模式**：`symbols_json` 保持 100 只 → `_worker_batch_specs` 整批重拉；重试成功 on-place reopen 同 batch_id（当前行为，不引 attempt 文件）。
- **symbol 模式**：`BatchSpec` 只带 failed-scope 的符号（可带收窄的日期窗口）→ 精确重拉；写盘用 `-attempt-{n}` 新批；成功后原 attempt 标 `superseded`（复用 `manifest.supersede_batches`，manifest.py:272）。
- **compact gate 两模式一致**：partial 数据只是"躺在 staging 等 failed-scope 清零"；scope 未清零，`compact_allowed`（compact_gate.py:34）照常锁死该 dataset，绝不 promote 半成品。门禁承诺（"这个 run 覆盖全量"）始终是批的完成性，与 staging 内已有多少行无关。

### D7. 盲点补缺：跨 run / 跨模式的机械与语义约束

实施前必须补齐的四个盲点：

1. **重试 scope 的机械接线（`_worker_batch_specs`）**。D5.1 让 `symbols_json` 保持全量、失败集放 `failed_scope_json`，但重试构建 scope 的 `_worker_batch_specs`（engine.py:465-481）**只读 `symbols_json`**——不改它，符号模式重试仍是整批重拉，D6 的"精确重拉"落空。符号模式分支必须**优先读 `failed_scope_json`**（含收窄的日期窗口），无则回退 `symbols_json`；`batch` 模式保持现状。
2. **D1（股票过滤）与 D4（粒度）解耦**。batch 模式若要"逐字节复刻 legacy"就得把 ETF 留在 universe，与 D1 冲突。决定：**D1 无条件生效**（任何粒度都只股票）；`daily_bars_granularity` 只管归因/落盘/重试语义。因此 batch 模式的"复刻"指**复刻整批 all-or-nothing 的归因与落盘语义**，而非"含 ETF 的窗口必然失败"。
3. **粒度开关的作用域声明**。`step_daily_bars_history`（同花顺，bars.py:1017）与 `step_daily_bars_delisted`（baostock，bars.py:1215）写入同一 `daily_bars` 数据集但**始终符号级**，不受开关影响——开关只作用于 worker-pool 的 TDX 主路径（`fetch_daily_bars_parallel`）及其 gap-fill。CLI 边界（config-only 派生）：**无 `--granularity` 参数**，故无"与 `--stale-only` 互斥"的 CLI 校验；`cne retry` 也**没有任何粒度覆盖入口**，永远用 run 记录的粒度。
4. **水位线-覆盖-审计的衔接保证**。水位线按**日**推进（`last_contiguous_dense_date` 只看 `_covered_days`，verify.py:272-283），**不感知符号覆盖**——防"单符号静默洞"的唯一机制是 compact gate（"failed-scope 未清零不放行"）。豁免符号的永久缺席因此不会挡水位线，但 **audit/lake_health 的期望集合必须 = 请求符号集 − 豁免集**，否则豁免符号会在独立审计路径上被报成缺口。这条随豁免一起落地。

## Mode 切换与数据生命周期（无需强制重置）

symbol ↔ batch 切换**不需要任何数据重置/清库**，四层隔离保证互不污染：

- **staging** 按 `run_id` 分区——新模式=新 run_id=新目录，互不覆盖；
- **manifest** 批按 `(run_id, batch_id)` 留痕，旧 failed 批只作审计，不挡新 run 的 compact gate（gate 按 run）；
- **curated** 已 compact 的行与模式无关，不回放、不重写；
- **重试隔离** 靠"粒度无 CLI 覆盖入口（config-only）+ run metadata 记录粒度 + `_retry_run` 复原"锁死，同一 run 永不混两种模式。

三条运维注意：

1. **孤儿 partial staging 会累积**：符号模式"scope 未清零"的 run 永不 compact（`_recover_compactable_backfill_staging` 也被 compact gate 挡下，main.py:942），partial staging 原地驻留直到 `cne clean`；主动弃用符号 run 后建议 `cne clean --force`。
2. **已 compact 数据不回放**：`incremental_window` 从水位线走、不回退；想用新模式重验某窗口必须显式 `cne backfill --start/--end`（或手工降水位线）。
3. **schema 迁移不是 reset**：`failed_scope_json` 走既有 `ALTER TABLE` 增补（manifest.py:120-126 同款）；旧 failed 批无该列 → 重试回退整批，安全。
4. **临时实验须改回 config**：粒度无 CLI 逃生口，临时切换 = 改共享配置 → 跑 → 改回，否则被 cron/launchd 复用的配置会把实验状态带进后续定时运行；run metadata 记录每个 run 的实际粒度，`cne status` 可审计。

## Risks / Trade-offs

- **依赖 ETF/REIT 日线的用户**：日更 tip 不再产出其 bars（与历史/backfill 口径一致；`daily_bars` 本就定义为 A 股股票日线）。有需要应另立基金日线数据集。
- **`list_date` NaT 导致豁免失效**：无法证明"未上市"的符号仍会判失败并走 failover（保守可取）；待 `list_date` 权威填充后豁免更完整。
- **符号级写盘引入部分批**：现在允许"一批内部分符号落盘"——需要保证 `manifest` 的 failed_symbols/failed-scope 与 staged 集合一致、重复批处理（clean/compact）能接受同一 run_id 下部分写入。这是本次实现的主要校验点。
- **attempt 文件覆盖陷阱**：[D5.2]——复用确定性 batch_id 重试会把首次部分文件整体覆盖。只按 attempt 命名 + `supersede_batches`，并加单测锁定（见 tasks 5.x）。
- **fetched_at keep=last 的源替换**：[D5.3]——重拉粒度不窄到符号×日期（或写盘前不 anti-join）时，后到备份行会替换已覆盖的主源行进 curated。必须保留 anti-join，回归测试锁 ADR-0005。
- **双路径维护成本**：`symbol/ batch` 两套语义在 worker/step/retry 各有一个分支点，长期并存增加维护面；用开关默认值 + 单测矩阵（同场景跑两模式）控住漂移。
- **默认值变更的行为影响**：默认从"整批 all-or-nothing"切到 `symbol`，对依赖"批失败=全无"观测习惯的用户（manifest/status 展示）有体感变化；`cne status`/告警口径需随 `failed_scope_json` 呈现，避免"100 FAILED"消失后信息真空。
- **`load_curated_instruments` 读一次**：日更每轮多一次本地读，代价可忽略。

## Migration Plan

1. 实现 D1 过滤 + 单测（universe 含 etf/stock/bj 时仅 stock 进入扫批；instruments 缺失时回退全量）。
2. 实现 D4 开关（Config 字段 + 校验 + 示例配置）、D2/D3 符号级归因与豁免（`symbol` 模式）、D5 failed-scope + attempt 文件 + anti-join、D6 重试分流；`batch` 模式保持现状分支。
3. datalake 重装 editable 后重跑 `cne run daily`（默认 `symbol`），确认无 100 symbols FAILED 的基金型批次；`cne sources tdx_protocol` 保持正常。
4. 切换验证（config-only）：`daily_bars_granularity = "batch"` → 跑 `cne run daily` 复现旧的 **整批 all-or-nothing 归因/落盘语义**（股票过滤 D1 仍无条件生效，不误以为"含 ETF 的窗口必然失败"）→ 跑后改回 `"symbol"`；单测矩阵同场景跑两模式断言语义差异。
5. 回滚 = 配置切回 `daily_bars_granularity = "batch"`（或撤销该过滤实现）。

## Open Questions

- **`list_date` 语义权威性**："挂牌日 vs 首个实际交易日"不同源口径不一（eastmoney clist vs baostock ipoDate）；若 `list_date` 记的是挂牌日而首个交易日更晚，"上市首日豁免"可能在应豁免时仍判失败。依赖 `list_date` 权威填充，或需 `trading_status`/成交证据辅助确认"已挂牌但未首次成交"。
- **停牌判别的时效**：当日新停牌在 `trading_status` 数据集尚未刷新、且占位串尚未累积到阈值（≥20 行）时，短期停牌的首日可能被误判为真缺口进入 failover；观察 failover 快照频率后定是否需要"首日宽容窗口"。
- **上市首日豁免的边界**：`list_date == 窗口结束` 才豁免；`list_date` 早于结束时窗口内的首日缺口按普通缺口严格处理，是否会造成上市初期（上市后头几个会话）的过判，观察后再定。