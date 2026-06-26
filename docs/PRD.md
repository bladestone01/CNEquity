# StockDataEngine 产品需求文档（PRD）

版本：v2.0
状态：Draft（Living Document）
日期：2026-06-28
产品定位：整合多方数据源的 A 股辅助数据平台 —— 采集、编排、标准化，交付可直接查询的 Parquet 数据湖

> 阅读约定：本文每条能力都标注**实现状态**，避免「愿景当现状」。
> 🟢 已实现可用　🟡 部分实现/骨架　🔴 仅设计未实现
> 这些标注是团队对齐「现在能交付什么、还差什么」的唯一事实来源，迭代中必须随代码同步更新。

---

## 1. 产品概述

### 1.1 一句话

StockDataEngine 在 mootdx（TDX 协议）与多个 HTTP 数据源之上，提供一个自研的采集编排层（Ingestion Orchestrator），并行采集、清洗、质检 A 股多方数据，最终以**带溯源的、列契约稳定的 Parquet 分区数据湖**交付，支持 DuckDB / Polars 直接查询，研究方无需自建数据库、无需通达信客户端。

### 1.2 要解决的核心问题

| 痛点 | 解法 | 状态 |
|------|------|------|
| 单个数据源（mootdx 等）只能「调接口」，不能「跑全市场、可恢复」 | Orchestrator：Job → Wave → Step → Task → Batch → Manifest | 🟡 |
| 单进程拉数慢 | 多进程按 symbol batch 并行 + 全局限速 | 🟡（并行已有，限速未实现） |
| 数据散落、口径不一、来源不可追溯 | 统一 schema 契约 + 分区 + provenance 列（source/data_version/fetched_at） | 🟡（契约定义有，写入未强校验） |
| 单一数据源不稳定、口径有偏差 | 多源采集 + canonical/snapshot 分离 + 跨源审计 | 🔴 |
| 研究方被迫自建并维护数据库 | curated Parquet 即交付物 + 可选 DuckDB 视图 | 🟢 |

### 1.3 目标用户与使用场景

| 角色 | 场景 | 关键诉求 |
|------|------|----------|
| 量化研究员 | 拉全市场日线/复权因子做特征工程 | 口径稳定、可复现、可追溯 |
| 数据工程师 | 每日定时增量更新、失败重跑 | 幂等、可恢复、可观测 |
| 个人投资者/开发者 | 本地一键起一套 A 股数据底座 | 安装简单、无需自建 DB |

### 1.4 产品边界

**In Scope（v1）**

- A 股及相关品种（含指数）综合数据采集与标准化
- Ingestion Orchestrator（多进程、断点续跑、运行清单）
- Parquet 数据湖（staging/curated/derived/meta 四层）+ 可选 DuckDB 视图
- CLI 运维（init/run/backfill/compact/derive/audit/status/retry/catalog/query）

**Out of Scope（v1）**

- 策略回测、信号生成、交易执行
- Tick / 逐笔 / 订单簿历史
- WebSocket / 实时推送
- Web 管理台、多租户 SaaS、权限体系
- 港股 / 美股（架构预留，不在 v1 交付）

### 1.5 非目标但必须明确的约束（合规与法务）

- 本平台**仅供个人研究**，所有数据来自第三方公开接口（mootdx/新浪/东方财富/巨潮等）。
- 必须尊重各数据源的访问频率与 ToS；默认配置走保守限速。东方财富 NID 等鉴权处理仅用于正常访问，**不得用于绕过付费墙或大规模抓取**。
- 数据版权归原始来源；交付物不得二次商业分发。
- 这一约束需在 README 与首次 `sde init` 提示中可见。

---

## 2. 命名与约定

| 维度 | 值 |
|------|-----|
| 产品名 | StockDataEngine |
| Python 包 | `stock_data_engine`（src layout） |
| CLI | `sde` |
| 配置示例 | `configs/stockdata.example.toml` |
| 默认 data root | `./data/stock-data-engine`（可配置，生产建议绝对路径） |
| DuckDB 文件 | `{data_root}/duckdb/stockdata.duckdb` |
| Symbol 格式 | `{code}.{SH\|SZ\|BJ}`，另设独立 `exchange` 列 |
| 时区 | 业务日期/时间一律 `Asia/Shanghai`；`fetched_at` 用 UTC ISO8601 |

详见 [schema.md](schema.md) / [datasets.md](datasets.md) / [operations.md](operations.md)。

---

## 3. 系统架构

### 3.1 分层架构

```
CLI (sde):  init | config validate | servers test | run | backfill
            compact | derive | audit | status | retry | catalog | query
                              │
        Ingestion Orchestrator（Step Registry + Wave Engine + Manifest）
                              │
        Worker Pool（ProcessPool × N，每进程独立数据源连接）
                              │
   Adapters: tdx_protocol(mootdx) | sina | eastmoney | cninfo | akshare
                              │
   Normalize(Polars) + Schema 强校验 → staging Parquet → compact → curated
                              │
   Derive / Audit / OnDemandService / DuckDB Views
```

### 3.2 技术栈（与 `pyproject.toml` 对齐）

| 组件 | 选型 | 说明 |
|------|------|------|
| 语言 | Python ≥ 3.11 | 内置 `tomllib`，无需 `tomli` |
| TDX 协议 | mootdx ≥ 0.11（可选 extra `[tdx]`） | 默认源，无则降级 mock |
| HTTP | httpx | 同步 client |
| 清洗 | Polars ≥ 1.0 | 所有 DataFrame 操作 |
| 列式存储 | Parquet（PyArrow，zstd） | 交付格式 |
| 查询 | DuckDB ≥ 1.0 | 视图层，可选 |
| 编排元数据 | SQLite（`meta/manifest.db`） | runs/batches |
| 配置 | TOML + 环境变量覆盖 | |
| CLI | Click ≥ 8.1 | |

### 3.3 模块职责（与源码目录一一对应）

| 模块 | 职责 |
|------|------|
| `config/` | TOML 加载 + 校验（待加强：step/group 引用校验） |
| `domain/` | symbol 解析、universe 规则、schema 契约与 provenance |
| `adapters/` | 各数据源协议封装（tdx_protocol/sina/eastmoney/...） |
| `orchestrator/` | engine（wave 执行）、manifest（运行清单）、registry（step 注册） |
| `steps/` | 内置 step 定义（数据集采集单元） |
| `workers/` | 多进程 symbol-batch 并行抓取 |
| `storage/` | staging 写入、compact 去重、curated 分区写入 |
| `derive/` | 派生数据集（adj_factors） |
| `quality/` | 审计与质量 findings |
| `duckdb/` | 视图维护 |
| `catalog/` | 目录初始化、on-demand 服务 |
| `cli/` | 命令入口 |

### 3.4 数据湖目录契约

```
{data_root}/
  staging/   {dataset}/run_id={run_id}/part-{batch_id}.parquet     # 本次 run 原始落地
  curated/   {dataset}/{partition_col}={value}/part-*.parquet      # 每 PK 一行 canonical
  derived/   adj_factors/trade_date=.../ ; daily_features/...       # 计算派生
  raw/       可选原始响应留存
  meta/
    manifest.db                                                    # 运行/批次清单
    state/                                       🔴 每数据集增量水位（待实现）
    source_snapshots/{dataset}/source=.../data_version=.../        🔴 多源快照
    quality/findings/ ; quality/source_diffs/                      🟡 findings 有，diffs 待实现
    on_demand/{dataset}/{symbol}.json                              🟢 on-demand 缓存
  duckdb/stockdata.duckdb
```

**Curated 语义（核心契约）：** 每 PK 一行 canonical 记录；`source` / `data_version` / `fetched_at` 是**列**而非分区键。多源差异不在 curated 内打架——备源快照进 `meta/source_snapshots`，由 `audit` 产出 `source_diffs`，**永不自动切源、永不静默覆盖 canonical**。

---

## 4. 综合数据清单

> 完整字段见 [schema.md](schema.md)，逐源限制见 [datasets.md](datasets.md)。

### 4.1 MVP-P0（v1 首批）

| ID | 名称 | 主源 | 备源 | 状态 |
|----|------|------|------|------|
| instruments | 证券主数据 | tdx_protocol | akshare | 🟡 真实拉取，list_date 缺失 |
| trading_calendar | 交易日历 | tdx_protocol | 交易所 CSV | 🔴 当前为 weekday mock |
| trading_status | 停复牌/ST | tdx_protocol | eastmoney | 🔴 当前为 mock |
| daily_bars | 股票未复权日线 | tdx_protocol | eastmoney | 🟡 单源真实，无分页/无全量回填 |
| index_bars | 指数日线 | tdx_protocol | eastmoney | 🟡 |
| corporate_actions | 分红送转/除权 | tdx_protocol | eastmoney | 🔴 当前返回空 |
| adj_factors | 复权因子（外部源对齐） | sina | — | 🟡 串行 HTTP，性能瓶颈 |

Meta：`ingestion_runs`, `ingestion_batches`（🟢）、`quality_findings`（🟡）

### 4.2 v1.0-full 第二批 🔴

fund_flow, northbound_holdings, northbound_flows, margin_trading, sector_members, valuation_metrics, announcement_index
（注：config 已引用对应 step 名，但 step 尚未注册，运行时会被静默 skip——见 §10 风险 R-11）

### 4.3 On-demand 层 🟡

announcement_body, stock_news, research_reports, financial_reports（按 symbol 首次查询拉取 + 本地缓存；当前多为 placeholder/部分实现）

---

## 5. Ingestion Orchestrator

### 5.1 核心概念

| 概念 | 定义 | 状态 |
|------|------|------|
| Step | 注册的数据集采集单元（`@register_step`，含 `depends_on`/`group`/`requires_workers`） | 🟢 |
| Wave | 一组按相同策略执行的 step | 🟢 |
| Task / Batch | 可并行最小工作单元（symbol batch × date chunk）及其执行记录 | 🟡 |
| Run | 一次 Job 实例，写入 manifest | 🟢 |

### 5.2 编排执行模型：目标态 vs 现状

**目标态（v1 应达成）**

1. **真正的依赖驱动**：engine 消费 `StepEntry.depends_on`，对 wave 内 step 做拓扑排序与缺失依赖校验，而非纯靠 config 手排顺序。
2. **wave 内并行**：`parallel=true` 的 wave 内多个 step / 多个 task 真并发执行（线程池处理 I/O 密集 step，进程池处理 CPU/抓取密集 step），并正确合并各 step 的 `context_updates`。
3. **批级清单与续跑**：worker pool 产出的每个 symbol-batch 都登记 manifest（含 symbols、窗口、retry_count），`retry` 只重跑 status=failed 的 batch。

**现状（必须如实告知团队）**

- 🔴 `depends_on` 已声明但 engine **未消费**，wave 顺序完全由 config 决定。
- 🔴 `parallel` 分支与顺序分支**都是串行 for 循环**；真正的并发只发生在 `daily_bars` step 内部的 `ProcessPoolExecutor`。且 parallel 分支**漏传 `context_updates`**（已知 bug，见 R-02）。
- 🔴 worker batch **不写 manifest**，manifest 粒度是「1 step = 1 batch」，因此 `retry` 实为「整 step 重跑」，达不到批级续跑。

> 这三项是 v1 架构收敛的核心工作项，对应里程碑 M1/M2。

### 5.3 Wave DAG 配置（daily job）

```toml
[[job.daily.waves]]
name = "reference"
parallel = true
steps = ["instruments", "trading_calendar", "trading_status"]

[[job.daily.waves]]
name = "corp_actions_to_bars"
parallel = false
steps = ["corporate_actions", "daily_bars"]

[[job.daily.waves]]
name = "parallel_core"
parallel = true
steps = ["index_bars"]

[[job.daily.waves]]
name = "finalize"
parallel = false
steps = ["compact", "derive_adj_factors", "audit"]
```

**依赖规则（目标态由 `depends_on` 表达并由 engine 强制）：**

- `daily_bars` 依赖 `instruments`（取 universe）与 `corporate_actions`（除权日 rebackfill）
- `derive_adj_factors` 依赖 `daily_bars` + `compact`（从 Sina 拉因子并对齐交易日）
- `corporate_actions` 输出 `symbols_to_rebackfill`，`daily_bars` 优先回补这些 symbol
- `compact` 仅在同一 dataset 全部 batch SUCCESS 后触发

### 5.4 schedule_groups（错峰分组）

```toml
[job.daily.groups.core]
at = "16:00"
steps = ["instruments","trading_calendar","trading_status","corporate_actions","daily_bars","index_bars","compact","derive_adj_factors"]

[job.daily.groups.capital]
at = "16:30"
steps = ["fund_flow","northbound_holdings","northbound_flows","margin_trading"]

[job.daily.groups.signals]
at = "17:00"
steps = ["dragon_tiger","block_trades"]
```

组间互斥、组内按 Wave 规则执行。🟡 `core` 组可跑（其引用 step 多已注册）；`capital`/`signals` 引用的 step 尚未注册（R-11）。

### 5.5 init Phases（首次建库）

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | trading_calendar + instruments | 🟡 |
| 2a | corporate_actions backfill | 🔴 |
| 2b | daily_bars 增量（近 5 日） | 🟡 |
| 2c | daily_bars 全量历史 | 🔴 受 mootdx `offset=800` 限制，需实现分页才能真正回填 |
| 3 | index_bars + trading_status | 🟡 |
| 4 | compact + derive_adj_factors + audit | 🟡 |

### 5.6 Manifest Schema（SQLite）

`ingestion_runs`（run_id, job_name, status, started/finished_at, rows, metadata_json）与 `ingestion_batches`（run_id, batch_id, task_id, dataset, status, symbols_json, window, rows, retry_count）。
🟡 现已建表与索引；待加：worker batch 入库、WAL 模式 + `busy_timeout`（多进程并发写）。见 `orchestrator/manifest.py`。

---

## 6. 横切关注点（Cross-cutting Requirements）

> 这一章是上一版 PRD 缺失、却决定平台能否长期可用的部分。

### 6.1 限速与反封禁 🔴（v1 必做）

- 每个数据源一个**全局令牌桶**（跨进程共享，建议用文件锁或 manager），参数来自 `[sources.*].min_interval_*` / `[tdx_protocol].min_interval_ms`。
- 指数退避重试（已有 `max_retries`/`retry_backoff_seconds` 配置项，需在 adapter 层真正使用）。
- 失败分类：可重试（超时/限流/5xx） vs 不可重试（参数错/4xx）。

### 6.2 增量与幂等 🔴（v1 必做）

- `meta/state/` 记录每数据集「上次成功覆盖到的 trade_date / report_period」水位。
- 增量 run 从水位续抓，而非写死 `trade_date - 5天`。
- 同一窗口重复 run 结果一致（compact 已按 PK + max(fetched_at) 去重，幂等性基本满足）。

### 6.3 数据契约强校验 🟡→🟢

- 写 staging 前按 `DATASET_SCHEMAS` 对列名/类型做 cast 与校验，缺列/类型不符直接判 batch failed（不静默写脏数据）。
- `fetched_at` 落 timestamp 类型（当前为字符串，与 schema.md 不符，需统一）。
- `data_version` 应反映源接口版本/抓取契约版本，而非恒为 `"v1"`。

### 6.4 多源 failover 与审计 🔴

- 主源失败 → 退避重试 → 仍失败标 batch failed → 可选备源抓取写入 `meta/source_snapshots`（不进 curated）。
- `audit` 比对 primary vs snapshot 产出 `source_diffs`，人工决策是否切源。
- 抽样跨源一致性检查（价格/成交量），偏差超阈值产 finding。

### 6.5 可观测性 🟡

- `sde status` 输出最近 run + batch 计数（🟢）。
- 待加：结构化运行报告导出（`.progress.json`）、关键阶段耗时、各数据源调用计数/失败率。
- 统一日志格式（已有 logging，建议结构化字段：run_id/step/dataset）。

### 6.6 配置健壮性 🔴

- `validate_config` 增加：所有 wave/group 引用的 step 必须在 registry 中存在；未知 step **报错而非静默 skip**。
- 校验 universe 取值、source 启用与依赖一致性。

### 6.7 安全 🟡

- 移除 `verify=False`（sina/derive 当前关闭了 TLS 校验，存在中间人风险）；如证书问题应显式配置 CA。
- 鉴权/指纹处理仅限正常访问，配置项默认保守。

---

## 7. Universe 定义

`universe.default = "all_a"` 规则：

- 市场：SH / SZ / BJ
- 前缀白名单：沪 `60/68`、深 `00/30`、北交所 `92`（⚠️ 北交所历史还含 `43/83/87/88` 等前缀，当前白名单仅 `92`，可能漏覆盖——待确认，见 R-12）
- 排除债券/转债前缀 `81–89`
- 全量覆盖 instruments；退市 symbol 保留历史 bars，标记 `delist_date`

Symbol 格式 `{code}.{SH|SZ|BJ}`，独立 `exchange` 列。

---

## 8. CLI

| 命令 | 功能 | 状态 |
|------|------|------|
| `sde init` | 初始化目录、manifest、DuckDB 视图 | 🟢 |
| `sde config validate` | 校验配置（待加强引用校验） | 🟡 |
| `sde servers test` | TDX 连通性测试 | 🟢 |
| `sde run daily [--group] [--backfill]` | 日更（Wave 或分组） | 🟡 |
| `sde backfill <dataset>` | 历史回填 | 🟡 |
| `sde compact` | staging → curated | 🟢 |
| `sde derive <name>` | 派生数据集 | 🟡 |
| `sde audit` | 质量审计 | 🟡 |
| `sde status` | 运行状态 | 🟢 |
| `sde retry --run-id` | 重试失败（当前为 step 级） | 🟡 |
| `sde catalog` | 数据目录概览 | 🟢 |
| `sde query [--sql] / [--dataset --symbol]` | DuckDB 查询或 on-demand 拉取 | 🟢 |

---

## 9. 质量与测试要求

| 项 | 现状 | 目标 |
|----|------|------|
| 单元测试 | 🟡 3 文件，mock 烟雾测试 | compact 去重正确性、因子对齐、symbol 规则、config 校验、retry 语义均需覆盖 |
| 集成测试 | 🔴 | mock 数据跑通 daily 全链路 + 断言 curated 分区/行数 |
| 数据质量 | 🟡 行数/空值检查 | PK 唯一性、分区完整性、跨源一致性 |
| CI | 🔴 | ruff + pytest 作为合并门禁 |

---

## 10. 风险登记册（Risk Register）

| ID | 风险 | 影响 | 缓解 |
|----|------|------|------|
| R-01 | "Wave DAG" 无依赖解析 | 配置顺序错即数据错 | M1 实现 `depends_on` 拓扑排序 |
| R-02 | parallel wave 实为串行且漏传 context | 性能不达标、context 丢失 | M1 修复 engine 并发与 context 合并 |
| R-03 | 批级 retry/续跑缺失 | 大 run 失败需整 step 重跑 | M2 worker batch 入 manifest |
| R-04 | 无全局限速 | 被数据源封禁 | M1 令牌桶限速 |
| R-05 | 无增量水位 | 重跑全量、效率低 | M2 `meta/state/` 水位 |
| R-06 | mootdx `offset=800` 无分页 | 全量历史回填不可达 | M2 分页抓取 |
| R-07 | adj_factors 串行 HTTP | 全市场分钟级跑不完 | M3 并行 + 缓存 |
| R-08 | failover/snapshot/diff 未实现 | 单源故障即断更 | M4 |
| R-09 | 写入无 schema 强校验 | 脏数据进湖 | M1 写前校验 |
| R-10 | `verify=False` | TLS 中间人风险 | M1 移除 |
| R-11 | capital/signals 等 step 未注册被静默 skip | 数据集悄悄缺失 | M1 未知 step 报错 + 逐步实现 |
| R-12 | 北交所前缀白名单可能漏覆盖 | universe 不全 | 确认 BSE 编码规则后修正 |
| R-13 | 第三方数据 ToS/版权 | 合规风险 | §1.5 约束 + 保守限速 |

---

## 11. 里程碑

| 阶段 | 交付 | 重点 |
|------|------|------|
| M0 | 脚手架、`sde init`、manifest、Orchestrator 骨架 | 🟢 已完成 |
| M1 | 编排收敛：真依赖解析 + wave 并行 + 全局限速 + 写前 schema 校验 + config 引用校验 + 去 `verify=False` | 修复 R-01/02/04/09/10/11 |
| M2 | 增量与续跑：watermark + 批级 manifest + mootdx 分页全量回填 + corporate_actions/calendar/status 真实化 | 修复 R-03/05/06 |
| M3 | HTTP 第二批（capital/valuation/announcement）+ adj_factors 并行化 | 修复 R-07 |
| M4 | failover/snapshot/source_diffs + 跨源一致性审计 + 连续 2 周稳定日更 | 修复 R-08 |

---

## 12. 成功指标（可度量、贴合现实）

| 指标 | v1 目标 | 度量方式 |
|------|---------|----------|
| MVP-P0 覆盖率 | 7/7 数据集均有 curated 分区且真实源（非 mock） | `sde catalog` + 抽查 source 列 |
| 日线日更成功率 | ≥ 99% batch（批级 manifest 上线后统计） | manifest 聚合 |
| 8 workers 日线增量耗时 | < 30 min（不含 adj_factors 串行部分） | run 报告耗时 |
| 全量回填可行性 | 能回填至 2016（分页生效后） | Phase 2c 实跑 |
| 跨源抽样一致率 | ≥ 99.5%（价格 ±10 bps） | audit source_diffs |
| 幂等性 | 同窗口重复 run，curated 行数与内容不变 | 集成测试断言 |
| PK 唯一性 | curated 每数据集 100% 唯一 | audit |

---

## 13. 参考与设计决策

- 借鉴 [ashare-data-warehouse](https://github.com/Troywww/ashare-data-warehouse) 的 Fetcher Registry、Wave/分组调度、on-demand 分层、东财鉴权思路、逐表 audit 文档风格。
- **不复用**其 DuckDB-only 存储与前复权 OHLCV 口径——本平台坚持 Parquet 数据湖 + 未复权 OHLCV + 独立复权因子（qfq/hfq 可在查询期组合），保证口径可追溯、可重算。
- 关键设计决策（ADR 摘要）：
  - 存储用 Parquet 数据湖而非数据库 → 交付物即文件、零运维、DuckDB/Polars 直查。
  - curated 单 PK canonical + 多源 snapshot 分离 → 杜绝静默切源、保证可审计。
  - 编排自研而非引入 Airflow/Prefect → v1 规模下降低依赖与部署成本；若未来调度复杂度上升再评估。
