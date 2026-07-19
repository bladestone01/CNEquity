# StockDataEngine 路线图

版本：v1.0
日期：2026-07-07
北极星：**StockWorkbench `wb daily` 每交易日实际使用，且其回测/信号所依据的数据可信。**

> 分工约定：[PRD](PRD.md) 管数据集需求、schema 契约与风险登记册（§10/§11 保持事实记录）；
> **本文管优先级与排期**——「先做什么、为什么、做到什么程度算完」。
> 差距分析的完整论证见 [architecture.md §4](architecture.md)，本文的 G1–G7 编号与其一致。
> PRD §11.1 v1.2 修复计划中未完成的 P1/P2 项已并入下表对应 Phase，不再单独排期。

## 总览

| Phase | 主题 | 服务的赚钱环节 | 估时 | 对齐 Workbench |
|-------|------|----------------|------|----------------|
| **A** | 数据可信止血 | 回测结论可信 | ~1 周 | 阻塞 M3（wb daily 闭环上线前必须完成 A1） |
| **B** | 实盘闭环运行保障 | 信号及时 | ~1–2 周 | 支撑 M3 日常化（每交易日实际使用） |
| **C** | 策略广度数据 | 扩大 alpha 来源 | 按需 | Phase 1.5 / Phase 2 |
| **D** | 长期健壮 | 降低运维成本 | 机会性 | —— |

原则不变：**先纵深后广度**。Phase A/B 完成前不新增任何数据集。

---

## Phase A 数据可信止血（~1 周）

回测数据说谎是当前最大的赚钱风险。三项任务全部有湖内实测证据（2026-07-07）。

### A1 根治 adj_factors hfq 历史断裂（G1，最高优先级）🟢 诊断完成，断裂已清零（2026-07-12）

原始现状：1479/6555（22.6%）股票 hfq 因子历史断裂，单日假收益达千倍级；Workbench 靠自建 quality guard（|adj_ret|>0.35 剔除）自保，回测 universe 被迫缩水 23%。已挂 task_8f0ab24f。

**诊断结论（2026-07-12，防线落地后全湖实测）**：**当前湖内已无真断裂**——先前的 1479 只在 derive 侧 20x 粗筛 + 数据重算后已清零（A2 重跑期间一并修复）。防线扫全湖（2016→2026-07）仅剩：

- **1 个"疑似断裂"实为假阳**：600733.SH（前锋股份→北汽蓝谷借壳）停牌 2018-02-01→09-26 约 8 个月，2018-09-27 复权 -37% 复牌；factor 2.34→8.19（×3.5）**恰等于 10送25 送股比例、因子完全正确**，-37% 是重组真实重定价。Workbench 的平阈值 guard 会误杀它，引擎防线加 **相邻交易日门控** 后正确豁免（大跳变仅在连续交易日才物理不可能）。
- **`missing_corporate_action`（G5）**：相邻交易日 raw 越限价且无除权记录 → warning。停牌复牌（bar 非相邻交易日）与 discontinuity 一样豁免；TDX 定点补全后剩余 16 条均为非相邻假阳性，已由相邻门控收口（2026-07-16）。

| 步骤 | 内容 | 状态 |
|------|------|------|
| 诊断 | 对断裂股分类归因：Sina 源序列断裂 vs 引擎对齐/缓存缺陷 vs 老股除权事件缺失 | 🟢 完成：真断裂 0；残留 36 为 corporate_actions 缺事件（下沉为 corp_actions 补全，见下） |
| 修复 | 落地 [ADR-0004](adr/0004-store-hfq-derive-qfq-at-query.md) 的 append-only 增量 merge；对源断裂股备源/事件法重算 | 🟢 append-only 已落地（2026-07-19）：水位后新分区只追加；除权日/新股/显式 refresh 按 symbol merge；`sde derive adj_factors --full` 全量重写 |
| 防线 🟢 已落地（2026-07-12） | `quality/cross_checks.py::adj_factor_reconciliation_findings` 接入 audit：按 `adj_close=close×factor`（实测逐字节相等）重建复权收益，与 corporate_actions 对账。**error** `adj_close_discontinuity`（\|adj_ret\|>0.35 且与 raw 背离、**且相邻交易日**——移植 Workbench guard 并改进：相邻门控豁免停牌复牌重定价，引擎自此为权威且比下游更准）；**warning** `missing_corporate_action`（复权连续但 raw 越限价跳动却无除权事件）。13 单测。实跑全湖：**0 error + 36 warning**（`sde audit --full` 就断裂而言 HEALTHY） | 🟢 |

**验收达成**：全市场 bar-to-bar |adj_ret| 极值扫描无「无事件解释的假收益」（0 真断裂）；Workbench quality guard 剔除数实测已 →1（且该 1 为假阳，引擎侧已正确豁免，Workbench guard 可后续放宽/退役）。✅ **遗留**：36 条缺登记除权 → 转 corporate_actions 源补全（并入 A1 诊断的「老股除权事件缺失」，root cause 在 corp_actions 源，非 adj_factors）。

### A2 valuation_metrics 历史回填（G2 的价值策略部分）🟢 已完成（2026-07-09）

背景：湖内曾仅 2026-07-07 一天。Workbench Phase 1 价值策略 = 估值分位，需要 ≥5 年 PE/PB 历史。

**方案（2026-07-08 拍板 baostock）**：EM `clist` 只有当日快照、无历史；财报科目仅 net_profit/revenue/roe（缺净资产与股本），自研派生走不通。改由 **baostock 作历史源**——per-symbol 每日 PE/PB/PS 回填至 2016。为 snapshot 语义数据集加 `DatasetSpec.backfill_source`，`sde backfill valuation_metrics` 由此放行；日更仍走 EM 快照。市值：`float_mv` 由 k-data `amount/(turn/100)` 派生，`total_mv` 由年报 Q4 `totalShare×close` asof（2026-07-16）。

**实跑教训（2026-07-08→09）**：首次全量被 baostock 限流断连、旧 adapter 静默跳过 90% symbol 仍报 success（fail-loud 缺陷）。已加固（commit ef1deac）：逐 symbol 重试 + 每 300 只重登 + 区分 query 错误/合法空 + 返回 failed_symbols；step 续跑（跳过已回填）。

**已完成（2026-07-09）**：湖内 valuation_metrics = **10.34M 行，5204/5206 symbol，2016-01-04 → 2026-07-08**；每只行数中位数 2498。600519.SH 2552 行、PE 16.4(2016)→73.3(2021 顶)→18.1(现)口径正确。仅 2 只次新股（301583.SZ / 688806.SH，list_date 未登记）无 baostock 覆盖。

**遗留（部分关闭 2026-07-16）**：历史 `float_mv` 由 k-data `amount/(turn/100)` 派生；`total_mv` 由年报 Q4 `totalShare×close` asof 派生。重跑 `sde backfill valuation_metrics` 会重写原先 null-MV 的 baostock 行。与 EM 当日快照的 source_diffs 交叉校验仍可选。

**验收达成**：全市场 2016 起 PE/PB 历史可用；标杆股口径正确；Workbench 估值分位因子可回测。✅

### A3 index_bars 缺口验证与补齐（G6b）🟢 已验证（2026-07-12）

现状：Workbench 记录 ~3% 交易日缺失（task_265958c4）；引擎 4152a08 修复了 index 抓取路径，可能已根治但未验证。

**验收达成**：对 trading_calendar 全交易日（2016-01-04→2026-07-10，2554 日）× 全部 8 个指数做覆盖率扫描——核心指数 000300/000905/000852 及 000001/000016/399001/399006 各 2553 bar、零内部缺口（唯一"缺"的 2026-07-10 是尚未入库的当日新鲜度滞后）；000688.SH（科创50）2019-12-31 起为指数成立日、非缺口。`audit.py::_index_bars_coverage_findings` 对全湖返回 0 finding。~3% 历史缺口已被 4152a08 根治，Workbench 基准对齐无 gap。✅

---

## Phase B 实盘闭环运行保障（~1–2 周）

Workbench M3（`wb daily`）上线后，引擎从「研究工具」变成「生产依赖」。这一 Phase 补齐 [architecture.md §2](architecture.md) 的第 6 层。运维手册见 [ops-runbook.md](ops-runbook.md)。

| # | 任务 | 缺口 | 验收 | 状态 |
|---|------|------|------|------|
| B1 | 调度落地：macOS launchd（或 cron）每交易日 16:05 起按 schedule_groups 错峰执行；遵守 `workers=1` 约束（mootdx 与 ProcessPool 不兼容） | G4 | 连续两周无人工干预日更成功率 ≥99%（PRD §12 指标首次真正可度量） | 🟢 脚本就绪（2026-07-09）：`scripts/daily_pipeline.sh` 串行跑 6 组 + `install_scheduler.sh` 生成 launchd plist（16:05）；`run daily` 加失败退出码。**待用户执行安装 + 两周实测成功率** |
| B2 | freshness SLO + 告警：`sde audit --full` 定时执行，UNHEALTHY / run failed / 水位滞后 → 非零退出 + 本地通知（macOS osascript 或邮件），失败当晚可见 | G4 | 人为注入一次失败，10 分钟内收到通知 | 🟢 已完成（2026-07-09）：`scripts/health_notify.sh` 包 `audit --full` + `status --datasets`，异常 osascript 通知 + 退出 1；pipeline 末尾串联 |
| B3 | 备份：manifest.db + `meta/state/` 每日快照（简单 tar 轮换即可） | G7 | 删库演练：从备份恢复水位与运行历史 | 🟢 已完成（2026-07-09）：`scripts/backup_meta.sh` sqlite `.backup` 一致性快照 + state/quality，14 天轮换；恢复步骤见 runbook |
| B4 | snapshot 数据集稳定积累：B1 上线即自动生效——估值/资金流/成分/行业自此每日 +1 分区，历史向前滚动 | G2 | 湖内分区数随交易日线性增长，audit 无 STALE | ⏳ 随 B1 安装自动生效 |
| B5 | audit 护栏补全（G5 剩余）：跨数据集对账扩展（bars×calendar 覆盖率、valuation×bars 市值合理性）；R-22 残留的分页 soft-error 改 fail-loud | G5 | 每类检查有单测 + 注入式集成测试 | 🟢 已完成（2026-07-09）：`quality/cross_checks.py` 加 daily_bars×calendar（非交易日 orphan / 交易日全市场零 bar，error 级）+ valuation×bars（无 bar 退市股 orphan / 覆盖率过低，warning）接入 audit；R-22 收口 EM clist（全 host 失败 + 中途分页截断）与 CNINFO regulatory 改 fail-loud。**orphan 根治（2026-07-16）**：`sde derive valuation_orphans` + backfill 入口自动 purge；日更/回填已 filter `load_bar_universe`。市值合理性待湖内 MV 回填落地后开启 |

---

## Phase C 策略广度数据（对齐 Workbench Phase 1.5 / 2）

按 Workbench 反向优先级（valuation > financial_statement_items > industry_members > fund_flow）排序；每项开工前先做**口径预验证**，避免重蹈北向覆辙。

| # | 任务 | 服务策略 | 验收 |
|---|------|----------|------|
| C1 | financial_statement_items PIT 质量抽查：announce_date 覆盖率、修正报表处理（同科目多次公告取 as_of 时点值） | 质量/成长因子（Phase 1.5） | 抽 20 只股 × 8 期财报，announce_date 与巨潮公告日一致 |
| C2 | index_constituents / industry_members 历史方案：行业中性化与指数增强需要**历史**归属，快照积累太慢；评估申万/中证历史成分源或第三方回填 | 行业中性、指数增强（Phase 1.5） | 2020 起月度行业归属可用；成分变更日对齐调样公告 |
| C3 | 北向/资金流口径预验证（G6a）：确认 2024-08 后可得口径（季末持股）对策略是否仍有信息量；不可行则明确放弃，不再挂在路线图上 | 资金流策略（Phase 2 守门员） | 一页结论：可用口径 + IC 初筛，或明确否决 |
| C4 🟡 机制就绪，待跑回填（2026-07-12） | trading_status 历史 ST（G3）：**baostock k-data 的每日 `isST` 标志**为历史源（同 A2 估值回填的源与会话机制）。新增 `adapters/baostock/st_history.py::fetch_st_history`（抽出共享会话驱动 `_session.py`，valuation 一并改用、行为不变）+ `step_trading_status` 的 `_backfill` 分支（`sde backfill trading_status`）：扫 all_a×有 bar 的票，ST 交易日落 `status="st"`（`isST` 二值、不分 ST/*ST，够 universe 过滤）；停牌仍由 bar-gap 派生，互不冲突。**断点续跑**：swept-symbol 标记（meta/state），throttle 掉的票重跑续扫（~85% 从不 ST、无行可依据数据存在性判断，故需显式标记）。7 单测 + 实源验证（000017.SZ 276 ST 日、茅台 0）。**待执行全市场回填**（~5200 票、数小时，同 A2） | 消除回测 universe 前视偏差 | 2016 起历史 ST 可过滤；audit 覆盖起点警告消除 |

---

## Phase D 长期健壮（机会性，不设截止）

| 任务 | 出处 |
|------|------|
| 消费层 lazy scan + 分区裁剪下推 | R-25（部分：adj_factors bar 加载、trading_calendar 日期裁剪；audit 全量 lazy 仍待） |
| source_snapshots 保留期清理；`read_latest` 增量化 | 🟢 已落地（2026-07-19）：`read_latest` 只读最新 run_id；`sde clean --snapshot-retention-days` 默认 14 天（保留每源最新一份） |
| `data_version` 真实语义（源接口/契约版本，而非恒 "v1"） | PRD §6.3 |
| trading_calendar 种子年度扩展流程化（当前 2027 到期） | audit calendar 前视警告 |
| 宏观（macro_indicators 月度指标补齐 R-22 备注）、舆情深化 | PRD §4.4 P2 |
| 硬编码参数配置化（行数突变阈值、batch_stale_seconds 等） | 架构评审遗留 |

---

## 不做清单（明确否决，避免隐性负债）

- **对外商业化**（SaaS/多租户/API 计费）：本项目赚钱路径 = 自用实盘，2026-07-07 拍板。
- Tick/逐笔/Level-2、实时推送、港股美股：维持 PRD §1.4 Out of Scope。
- 引入 Airflow/Prefect：自研编排在单人规模下运维成本更低，除非调度复杂度质变。
- 大师 skills / 不可量化策略：Workbench 侧决策，引擎不为其新增数据集。

## 复盘节奏

每完成一个 Phase：① 用湖内实测更新本文与 [architecture.md §4](architecture.md) 的证据行；② PRD §10/§11 同步风险状态；③ Workbench 侧跑一次全市场回测对照，确认修复反映到资金曲线口径上。
