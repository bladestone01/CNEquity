# StockDataEngine 产品需求文档（PRD）

版本：v2.2（v2.1 基础上完成 2026-07-06 全库架构评审：状态标注与代码同步；新增风险 R-15–R-25 与 §11.1 v1.2 修复计划）
状态：Draft（Living Document）
日期：2026-07-06
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
| 单进程拉数慢 | 多进程按 symbol batch 并行 + 全局限速 | 🟢 |
| 数据散落、口径不一、来源不可追溯 | 统一 schema 契约 + 分区 + provenance 列（source/data_version/fetched_at） | 🟢（写前强校验 + mock 门控） |
| 单一数据源不稳定、口径有偏差 | 多源采集 + canonical/snapshot 分离 + 跨源审计 | 🟢 M4（snapshot + source_diffs 已实现；corporate_actions daily 主源路径有缺陷，见 R-17） |
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
| 时区 | 业务日期/时间一律 `Asia/Shanghai`；`fetched_at` 为 UTC timestamp（`Datetime(us, UTC)`） |

详见附录 A（Schema 契约）/ 附录 B（数据集目录）/ 附录 C（运维手册）。

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
| TDX 协议 | mootdx ≥ 0.11（可选 extra `[tdx]`） | 默认源；缺失/失败即判 batch failed（mock 仅限 `allow_mock` 测试开关） |
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
| `config/` | TOML 加载 + 校验（含 step/group 引用校验） |
| `domain/` | symbol 解析、universe 规则、schema 契约与 provenance、限速原语 |
| `adapters/` | 各数据源协议封装（tdx_protocol/sina/eastmoney/...）+ `throttle.py` 按源限速调度 |
| `orchestrator/` | engine（wave 执行）、manifest（运行清单）、registry（step 注册）、deps（拓扑排序）、worker_pool（多进程 symbol-batch 并行） |
| `steps/` | 内置 step 定义，**按 §4.0 数据层次一层一模块**：reference(L0)/bars(L1)/events(L2)/finalize；后续 capital(L4)/fundamentals(L3)/... 按层新增 |
| `storage/` | staging 写入、compact 去重、curated 分区写入、数据湖目录布局（layout） |
| `derive/` | 派生数据集（adj_factors） |
| `quality/` | 审计与质量 findings |
| `query/` | 消费层：DuckDB 视图、on-demand 服务；后续 Python 读取 API（reader）落此处 |
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
    state/{dataset}.json                         🟢 每数据集增量水位（部分失败仍推进，见 R-18）
    source_snapshots/{dataset}/source=.../data_version=.../        🟢 多源快照（M4）
    quality/findings/ ; quality/source_diffs/                      🟢 audit 每 run 产出两者
    adj_factors_cache/                                             🟢 复权因子逐 symbol 缓存
    on_demand/{dataset}/{symbol}.json                              🟢 on-demand 缓存
  duckdb/stockdata.duckdb
```

**Curated 语义（核心契约）：** 每 PK 一行 canonical 记录；`source` / `data_version` / `fetched_at` 是**列**而非分区键。多源差异不在 curated 内打架——备源快照进 `meta/source_snapshots`，由 `audit` 产出 `source_diffs`，**永不自动切源、永不静默覆盖 canonical**。

---

## 4. 综合数据清单

> 完整字段见附录 A，逐源限制见附录 B。
> 本章按**选股分析**所需的数据层次组织；每条数据集标注 ingestion 模式（batch / on-demand / derived）与目标里程碑。

### 4.0 数据分层与选股用途

| 层次 | 说明 | 典型选股用途 | 代表数据集 |
|------|------|--------------|------------|
| L0 基础参考 | Universe、日历、交易状态 | 可交易过滤、回测窗口、ST/停牌剔除 | instruments, trading_calendar, trading_status |
| L1 行情 | 价量时序（未复权 + 复权因子） | 动量、波动、量价、技术特征 | daily_bars, index_bars, adj_factors |
| L2 公司事件 | 除权除息、公告索引 | 事件驱动、除权回补、公告触发 | corporate_actions, announcement_index |
| L3 基本面 | 财报科目、估值、一致预期 | 价值/质量/成长因子 | financial_statement_items, valuation_metrics |
| L4 资金面 | 北向、融资、主力、龙虎榜 | 聪明钱、流动性、情绪 proxy | fund_flow, northbound_*, margin_trading, dragon_tiger |
| L5 结构与行业 | 板块、指数成分、行业分类 | 行业中性、板块轮动、相对强度 | sector_members, industry_members, index_constituents |
| L6 宏观 | 利率、货币、景气指标 | 宏观择时、风险预算、风格切换 | macro_indicators |
| L7 舆情与文本 | 新闻、研报、结构化情绪 | NLP 特征、事件挖掘、风险预警 | stock_news, sentiment_scores, research_reports |
| L8 风险合规 | 监管处罚、解禁、退市预警 | 负面清单、风控过滤 | share_unlock_schedule, regulatory_events |

**Ingestion 模式约定**

| 模式 | 适用场景 | 落地路径 |
|------|----------|----------|
| **batch** | 全市场、日更/周更、结构化、体积可控 | daily Wave → staging → curated |
| **on-demand** | 按 symbol 访问、体积大、更新稀疏 | `OnDemandService` → `meta/on_demand/` 缓存 |
| **derived** | 由 curated 计算、可重算 | `derive/` 或 DuckDB 视图 |

### 4.1 MVP-P0（v1 首批）

| ID | 名称 | 层次 | 模式 | 主源 | 备源 | 选股用途 | 状态 |
|----|------|------|------|------|------|----------|------|
| instruments | 证券主数据 | L0 | batch | tdx_protocol | akshare | Universe 定义、上市/退市过滤 | 🟡 真实拉取；list_date/delist_date 恒空 + compact 整表覆盖丢退市股（R-16） |
| trading_calendar | 交易日历 | L0 | batch | 交易所种子 CSV（2016–2027） | 指数 bars 推导 | 交易日对齐、特征窗口 | 🟢 M2 真实化 |
| trading_status | 停复牌/ST | L0 | batch | eastmoney（ST 板 + 停牌接口） | — | ST/*ST/停牌剔除 | 🟢 M2 真实化 |
| daily_bars | 股票未复权日线 | L1 | batch | tdx_protocol | eastmoney（snapshot） | 动量、波动、量价因子 | 🟡 分页回填可用，但增量无早停、每日翻全历史（R-19） |
| index_bars | 指数日线 | L1 | batch | tdx_protocol | eastmoney | 市场状态、Beta、相对强度基准 | 🟢 |
| corporate_actions | 分红送转/除权 | L2 | batch | tdx_protocol（backfill）/ eastmoney（daily） | eastmoney snapshot | 除权回补、股息因子 | 🔴 daily 路径失效：failover 开启必抛错、关闭则被 source 过滤清空（R-17） |
| adj_factors | 复权因子 | L1 | derived | sina | — | 前/后复权价、长期动量 | 🟡 已并行 + 缓存，但每日全量刷新并重写全部历史分区（R-20） |

Meta：`ingestion_runs`, `ingestion_batches`（🟢，daily_bars 批级）、`quality_findings` + `source_diffs`（🟢，audit 仅覆盖 4 个数据集，见 §9）

### 4.2 v1.0-full 第二批（batch）🟡

目标里程碑 **M3**——adapter/schema/step 均已实现并注册（config 校验通过）。
⚠️ 但所有分组运行（capital/signals 等）不含 compact step，抓到的数据滞留 staging、
**不会进入 curated**，`load()` 读不到——见 §10 风险 R-15，v1.2 P0 修复。

| ID | 名称 | 层次 | 主源 | 备源 | 选股用途 | PK（摘要） | 状态 |
|----|------|------|------|------|----------|------------|------|
| fund_flow | 个股资金流向 | L4 | eastmoney | akshare | 主力净流入、资金动量 | (symbol, trade_date) | 🟡 已实现，受 R-15 |
| northbound_holdings | 北向持股 | L4 | eastmoney | — | 外资偏好、持股变化 | (symbol, trade_date, channel) | 🟡 已实现，受 R-15 |
| northbound_flows | 北向净流入 | L4 | eastmoney | — | 外资流向、市场宽度 | (trade_date, channel) | 🟡 已实现，受 R-15 |
| margin_trading | 融资融券 | L4 | eastmoney | akshare | 杠杆情绪、融资买入 | (symbol, trade_date) | 🟡 已实现，受 R-15 |
| sector_members | 板块成分 | L5 | eastmoney | tdx_protocol | 板块归属、主题选股 | (symbol, sector_code, as_of_date) | 🟡 已实现，受 R-15 |
| valuation_metrics | 估值指标 | L3 | eastmoney | tencent | PE/PB/PS、价值因子 | (symbol, trade_date) | 🟡 已实现，受 R-15 |
| announcement_index | 公告索引 | L2 | cninfo | — | 事件触发、公告类型过滤 | (announcement_id) | 🟡 已实现，受 R-15 |

**schedule_groups 同批 step（signals 组）：**

| ID | 名称 | 层次 | 主源 | 选股用途 | 状态 |
|----|------|------|------|----------|------|
| dragon_tiger | 龙虎榜 | L4 | eastmoney | 机构/游资席位、短线情绪 | 🟡 已实现，受 R-15；BJ 市场行被误过滤 |
| block_trades | 大宗交易 | L4 | eastmoney | 折价率、大股东减持信号 | 🟡 已实现，受 R-15 |

### 4.3 On-demand 层 🟡

按 symbol 首次查询拉取 + 本地缓存（`meta/on_demand/`）；适合体积大、访问稀疏的数据。当前多为 placeholder/部分实现。

| ID | 名称 | 层次 | 主源 | 选股用途 | 状态 |
|----|------|------|------|----------|------|
| announcement_body | 公告正文 | L2 | cninfo | 事件 NLP、关键词挖掘 | 🟡 |
| stock_news | 个股新闻 | L7 | eastmoney / akshare | 舆情事件、主题关联 | 🟢 on-demand |
| research_reports | 研报摘要 | L7 | eastmoney reportapi | 分析师观点、评级变化 | 🟡 |
| financial_reports | 财报原文/PDF | L3 | sina / gpcw | 深度基本面解析 | 🟡 |

### 4.4 v1.1 扩展 batch（选股导向）🟡

schema/adapter/step 已全部实现（fundamentals/macro_risk/research 三个 schedule group）。
⚠️ 同受 R-15 影响：分组运行不 compact，数据滞留 staging。此外 macro_indicators 的
akshare 月度指标仅在发布日恰为运行日时才入库（几乎取不到，见 R-22 备注）。

| ID | 名称 | 层次 | 更新频率 | 主源 | 选股用途 | PK（摘要） | 优先级 |
|----|------|------|----------|------|----------|------------|--------|
| financial_statement_items | 财报核心科目 | L3 | 季报披露后 | eastmoney / akshare | ROE、负债率、现金流质量因子 | (symbol, report_period, statement_type, item_code) | P0 |
| index_constituents | 指数成分与权重 | L5 | 指数调样日 | csindex / eastmoney | 指数增强、成分股池、权重因子 | (index_symbol, symbol, as_of_date) | P0 |
| industry_members | 行业分类归属 | L5 | 月度 | eastmoney / 申万 | 行业中性、板块轮动 | (symbol, classification_system, as_of_date) | P1 |
| macro_indicators | 宏观指标 | L6 | 发布日 | akshare / 官方 | 宏观择时、利率敏感风格 | (indicator_id, obs_date) | P1 |
| market_breadth | 市场宽度指标 | L7 | 日更 | 自算 + eastmoney | 涨跌家数、涨停比、情绪极值 | (trade_date, metric_id) | P1 |
| share_unlock_schedule | 限售解禁日历 | L8 | 日更 | eastmoney | 供给冲击、解禁压力因子 | (symbol, unlock_date) | P1 |
| regulatory_events | 监管处罚/立案 | L8 | 事件驱动 | cninfo / 交易所 | 负面清单、合规风控 | (event_id) | P2 |
| institutional_holdings | 机构持股（基金/QFII 等） | L4 | 季报 | eastmoney | 机构共识、持仓变化 | (symbol, holder_type, report_period) | P2 |
| analyst_consensus | 一致预期盈利 | L3 | 日更/周更 | eastmoney | EPS 修正、预期差因子 | (symbol, forecast_date) | P2 |
| sentiment_scores | 结构化情绪得分 | L7 | 日更 | NLP on stock_news + announcements | 情绪因子、舆情反转 | (symbol, trade_date, score_channel) | P2 |

**macro_indicators 首批指标（建议）**

| indicator_id | 说明 | 频率 |
|--------------|------|------|
| shibor_3m | 3 个月 SHIBOR | 日 |
| lpr_1y | 1 年期 LPR | 月 |
| cnbond_yield_10y | 10 年期国债收益率 | 日 |
| pmi_manufacturing | 制造业 PMI | 月 |
| m2_yoy | M2 同比增速 | 月 |
| social_financing | 社融增量 | 月 |

**financial_statement_items 首批科目（建议 batch 化）**

利润表：营收、归母净利润、扣非净利润；资产负债表：总资产、总负债、净资产；现金流量表：经营现金流净额；衍生：ROE、资产负债率、经营现金流/净利润（可在 derive 层计算）。

### 4.5 因子 ↔ 数据集映射（选股消费参考）

下游选股/因子工程可直接参照此表组合 curated 数据集；**不在 StockDataEngine 内实现因子逻辑**。

| 因子类别 | 依赖数据集 | 备注 |
|----------|------------|------|
| 动量 / 反转 | daily_bars + adj_factors | 查询期组合 adj_close |
| 波动 / 流动性 | daily_bars | amount、换手率需结合 float 市值（valuation_metrics） |
| 价值 | valuation_metrics | PE/PB/PS/PCF |
| 质量 | financial_statement_items | ROE、杠杆、现金流质量 |
| 成长 | financial_statement_items | 同比/环比增速 |
| 资金 | fund_flow, northbound_*, margin_trading, dragon_tiger | M3 交付 |
| 事件 | corporate_actions, announcement_index, regulatory_events | 除权日触发 rebackfill |
| 行业中性 | industry_members / sector_members, index_constituents | 中性化基准 |
| 宏观暴露 | macro_indicators | 组合层风险预算 |
| 情绪 | market_breadth, sentiment_scores, stock_news | batch 优先，news 作 NLP 输入 |
| 风控过滤 | trading_status, share_unlock_schedule, regulatory_events | Universe 黑名单 |

### 4.6 数据边界（v1 不纳入）

| 类别 | 原因 |
|------|------|
| Tick / 逐笔 / 订单簿历史 | 存储与带宽成本；见 §1.4 Out of Scope |
| 实时推送 / WebSocket | 产品定位为离线研究数据层 |
| 港股 / 美股 | 架构预留 exchange 列，v1 不交付 |
| 社交媒体全量爬取（微博/雪球帖文） | ToS/合规风险高；v1 仅 structured + 新闻标题/on-demand |
|  Level-2 十档快照历史 | 同 Tick，Out of Scope |

### 4.7 里程碑与数据集交付顺序

| 里程碑 | 交付数据集 | 选股能力解锁 |
|--------|------------|--------------|
| M2 完成 | §4.1 全部真实化 + 全量日线回填 | 全市场价量因子、复权研究 |
| M3 完成 | §4.2 + dragon_tiger + block_trades | 资金/估值/公告事件选股 |
| M3+ | §4.4 P0（financial_statement_items, index_constituents） | 价值/质量/指数增强 |
| v1.1 | §4.4 P1–P2 + macro / sentiment batch | 宏观择时、情绪、风控完整闭环 |
| 持续 | §4.3 on-demand 深化 | 深度文本/NLP 研究 |

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

**现状**

- 🟢 `depends_on` 由 engine 消费：wave 内按 `step_execution_levels` 拓扑分层执行（`orchestrator/deps.py`），未注册 step 直接报错（`validate_steps_registered`）。
  ⚠️ 但 `compact`/`audit` 未声明 `depends_on`，拓扑排序按字母序把它们排进第 0 层：
  finalize wave 中 **audit 先于 compact 执行**（本次 run 数据从未被审计），分组运行中
  compact 在抓取 step 之前空跑——见 R-15 / R-23。
- 🟢 `parallel` wave 内同层 step 走 `ThreadPoolExecutor` 真并发，`context_updates` 加锁合并；`daily_bars` step 内部另有 `ProcessPoolExecutor` symbol-batch 并行。
- 🟡 worker batch **已写 manifest**（daily_bars symbol-batch 粒度，含 symbols/window），`retry` 对 daily_bars 只重跑失败 batch；其余 step 仍是「1 step = 1 batch」整步重跑。遗留缺口：dataset 名单硬编码 `daily_bars`、retry 成功后不触发 compact（重抓数据滞留 staging）、`retry_count` 恒为 0——见 R-18。

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
- `corporate_actions` 输出 `symbols_to_rebackfill`，`daily_bars` 优先回补这些 symbol（⚠️ daily 路径当前失效，见 R-17）
- `compact` 仅在同一 dataset 全部 batch SUCCESS 后触发（⚠️ 目标态；当前无门禁，见 R-18）

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
| 1 | trading_calendar + instruments | 🟢 |
| 2a | corporate_actions backfill（mootdx xdxr 逐 symbol） | 🟢 |
| 2b | daily_bars 增量（水位续抓） | 🟢 |
| 2c | daily_bars 全量历史（800 条分页） | 🟢 分页已实现 |
| 3 | index_bars + trading_status | 🟢 |
| 4 | compact + derive_adj_factors + audit | 🟡 audit 排序缺陷（R-23）；phase 间 context 不传递，init 期间 rebackfill 信号丢失 |

注：`run_init_phases` 各 phase 复用同一 run_id 但逐 phase 调 `finish_run`（状态被反复覆盖），
且 phase2c 的 `backfill=True` 元数据会被首个 phase 的元数据覆盖（仅记录错误，不影响实际回填行为）。

### 5.6 Manifest Schema（SQLite）

`ingestion_runs`（run_id, job_name, status, started/finished_at, rows, metadata_json）与 `ingestion_batches`（run_id, batch_id, task_id, dataset, status, symbols_json, window, rows, retry_count）。
🟢 建表 + 索引 + WAL + `busy_timeout=5000` 均已实现；daily_bars worker batch 入库。
遗留：`retry_count` 从未累计（`start_batch` INSERT OR REPLACE 归零）；retry 收尾的 `finish_run` 会把 rows_read/written 覆盖为 0。见 `orchestrator/manifest.py`。

---

## 6. 横切关注点（Cross-cutting Requirements）

> 这一章是上一版 PRD 缺失、却决定平台能否长期可用的部分。

### 6.1 限速与反封禁 🟡

- 🟢 每数据源跨进程限速：文件锁 + 状态文件（`domain/rate_limit.py`），参数来自 `[sources.*].min_interval_*` / `[tdx_protocol].min_interval_ms`，已接入 tdx adapter 与 worker pool。
  ⚠️ 但 EastMoney 各 step 走 `EastMoneyClient` 的**进程内 per-实例**节流，未接入跨进程限速；
  capital 组 7 个 step 同层并发时对 EM 实际是 7 req/s 叠加——见 R-21。
- 🔴 指数退避重试（已有 `max_retries`/`retry_backoff_seconds` 配置项，adapter 层从未使用）。
- 🔴 失败分类：可重试（超时/限流/5xx） vs 不可重试（参数错/4xx）。
- 🔴 HTTP 分页中途失败被静默截断（`fetch_datacenter`/`fetch_clist_pages`/`fetch_bars_paginated`
  遇错 `break`），半截数据仍判 batch success——违反「永不伪造」原则的同族问题，见 R-22。

### 6.2 增量与幂等 🟡

- 🟢 `meta/state/{dataset}.json` 水位已实现；增量 run 从水位 +1 续抓（`steps/common.py`）。
- 🟢 同一窗口重复 run 结果一致（compact 按 PK + max(fetched_at) 去重）。
- 🔴 **部分失败仍推进水位**：compact 不检查本 run 是否有 failed batch，成功批次把
  max(trade_date) 推到今天 → 失败 symbol 当日数据形成**永久空洞**（除非人工 `sde retry`）。
  「compact 仅在同一 dataset 全部 batch SUCCESS 后触发」的契约未实现——见 R-18。
- ⚠️ 水位计算方式为「读整个 curated 数据集求 max(分区列)」，随湖增长每日全量扫描——见 R-25。

### 6.3 数据契约强校验 🟢（data_version 语义除外）

- 🟢 写 staging 前按 `DATASET_SCHEMAS` 对列名/类型做 cast 与校验（`storage/parquet.py` → `validate_dataframe`），缺列直接判 batch failed。
- 🟢 `fetched_at` 已统一为 UTC timestamp 类型（`Datetime("us","UTC")`）；compact 读旧文件时自动归一。
- 🟢 **mock 数据门控**：数据源失败默认抛 `TdxSourceError` 判 batch failed，**永不静默伪造数据**；仅 `[tdx_protocol].allow_mock = true`（测试/演示）时返回 mock，且标记 `source="mock"`，audit 对 curated 中 mock 行产 error finding。
- 🔴 `data_version` 应反映源接口版本/抓取契约版本，而非恒为 `"v1"`。

### 6.4 多源 failover 与审计 🟢（M4 已实现，遗留缺口见下）

- 🟢 daily_bars batch 失败 → EastMoney 备源写入 `meta/source_snapshots`（不进 curated）；corporate_actions 每 run 备源快照。
- 🟢 `audit` 比对 primary vs snapshot 产出 `meta/quality/source_diffs/{run_id}.json`（close ±10bps 抽样）。
- 遗留：主源失败**无退避重试**直接判 failed；worker 进程内的 failover 逻辑与
  `quality/failover.py` 重复实现且不读 `[failover.datasets]` 配置、不限速；
  snapshot 目录按 run_id 无限累积，`read_latest` 全量 concat 越跑越慢。

### 6.5 可观测性 🟡

- `sde status` 输出最近 run + batch 计数（🟢）。
- 待加：结构化运行报告导出（`.progress.json`）、关键阶段耗时、各数据源调用计数/失败率。
- 统一日志格式（已有 logging，建议结构化字段：run_id/step/dataset）。

### 6.6 配置健壮性 🟡

- 🟢 `validate_config` 校验所有 wave/group 引用的 step 必须在 registry 中存在；engine 运行前 `validate_steps_registered` 对未知 step **报错而非静默 skip**。
- 🔴 校验 universe 取值、source 启用与依赖一致性。

### 6.7 安全 🟢

- 🟢 `verify=False` 已移除（代码中无 TLS 校验关闭点）；如未来出现证书问题应显式配置 CA，而非关闭校验。
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
| `sde run daily [--group] [--backfill]` | 日更（Wave 或分组） | 🟡 分组模式产出不落 curated（R-15） |
| `sde backfill <dataset>` | 历史回填 | 🟡 回填后不 compact（R-26） |
| `sde compact` | staging → curated | 🟡 CLI 版仅处理 daily_bars（R-26） |
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
| 单元测试 | 🟡 18 文件（M2–M4/v1.1 各批次均有），但多为 mock 烟测 | 补：分组 run→curated 端到端、部分失败水位不推进、audit 执行顺序、compact 幂等 |
| 集成测试 | 🟡 有 mock 全链路，但断言弱（`status in ("success","failed")` 恒真） | 断言 curated 分区/行数/来源列 |
| 数据质量 | 🟡 audit 仅覆盖 4 个数据集，PK 检查仅取前 20 个文件 | 全部 25 个数据集 + 全量 PK/分区完整性/跨源一致性 |
| CI | 🟢 GitHub Actions：ruff check/format + pytest（3.11/3.12） | 增加 coverage 门槛 |

---

## 10. 风险登记册（Risk Register）

| ID | 风险 | 影响 | 缓解 | 状态 |
|----|------|------|------|------|
| R-01 | "Wave DAG" 无依赖解析 | 配置顺序错即数据错 | `depends_on` 拓扑排序（`orchestrator/deps.py`） | 🟢 已修复 |
| R-02 | parallel wave 实为串行且漏传 context | 性能不达标、context 丢失 | engine ThreadPool 并发 + context 加锁合并 | 🟢 已修复 |
| R-03 | 批级 retry/续跑缺失 | 大 run 失败需整 step 重跑 | worker batch 入 manifest（M2） | 🟡 daily_bars 批级已实现；dataset 硬编码、retry 不 compact、retry_count 不累计（并入 R-18） |
| R-04 | 无全局限速 | 被数据源封禁 | 跨进程文件锁限速（`domain/rate_limit.py`） | 🟢 已修复（EM 侧缺口另见 R-21） |
| R-05 | 无增量水位 | 重跑全量、效率低 | `meta/state/` 水位（M2） | 🟢 已实现（部分失败推进缺陷另见 R-18） |
| R-06 | mootdx `offset=800` 无分页 | 全量历史回填不可达 | 分页抓取（M2） | 🟢 已实现（增量无早停另见 R-19） |
| R-07 | adj_factors 串行 HTTP | 全市场分钟级跑不完 | 并行 + 缓存（M3） | 🟡 已并行 + 缓存；但缓存每日必失效、qfq 全历史重写（并入 R-20） |
| R-08 | failover/snapshot/diff 未实现 | 单源故障即断更 | M4 | 🟢 已实现（遗留缺口见 §6.4） |
| R-09 | 写入无 schema 强校验 | 脏数据进湖 | 写前 `validate_dataframe` 强校验 | 🟢 已修复（cast `strict=False` 会把脏值静默置 null，注意） |
| R-10 | `verify=False` | TLS 中间人风险 | 已移除 | 🟢 已修复 |
| R-11 | capital/signals 等 step 未注册被静默 skip | 数据集悄悄缺失 | `validate_config`/`validate_steps_registered` 报错 | 🟢 已修复；step 已全部注册 |
| R-12 | 北交所前缀白名单可能漏覆盖（仅 `92`，历史含 `43/83/87/88`） | universe 不全 | 确认 BSE 编码规则后修正 | 🔴 |
| R-13 | 第三方数据 ToS/版权 | 合规风险 | 保守限速默认值 | 🟡 持续 |
| R-14 | 数据源失败静默返回 mock 假数据入湖 | 下游选股被投毒 | 默认 fail-loud；mock 仅限 `allow_mock` 且标记 `source="mock"` + audit 拦截 | 🟢 已修复 |

**2026-07-06 架构评审新增（修复计划见 §11.1）：**

| ID | 风险 | 影响 | 缓解 | 状态 |
|----|------|------|------|------|
| R-15 | **分组运行不 compact**：capital/signals/fundamentals/macro_risk/research 组无 compact step；core 组虽含 compact 但其无 `depends_on` 被拓扑排到第 0 层先空跑。compact 只处理当前 run_id 的 staging，跨 run 无法补救 | **高**：M3/v1.1 全部数据集滞留 staging，curated 不更新，`load()` 读空；README 推荐的生产 cron 全部失效 | engine 对每个 run 在末尾自动追加 compact（+audit），或给 compact 动态注入对本 run 全部数据集 step 的依赖 | 🔴 P0 |
| R-16 | instruments compact 用本次抓取**整表覆盖** curated，不与旧数据合并；TDX 只返回在市股票且 list/delist_date 恒空 | **高**：退市股从 universe 消失 → **幸存者偏差**；「保留退市 symbol」契约未实现；list/delist 过滤空转 | compact 改 merge+PK 去重保留旧行；用 EM/交易所数据回填 list_date/delist_date | 🔴 P0 |
| R-17 | corporate_actions daily 路径自相矛盾：daily 模式不传 symbols（跳过 TDX 分支），failover 开启时 `primary_only=True` 又跳过 EM 分支 → 必抛错；failover 关闭时 EM 行被 `source=="tdx_protocol"` 过滤清空 | **高**：默认配置下 daily run 每天失败；除权日 `symbols_to_rebackfill` 永远为空，除权回补失效 | 明确 per-dataset 主源：daily 用 EM 按日接口作 canonical（改 ADR-0003 为「每数据集声明主源」），backfill 用 TDX xdxr；或 daily 对当日除权 symbol 补 TDX 拉取 | 🔴 P0 |
| R-18 | 部分批失败仍推进：engine 波次失败后继续执行 finalize，compact 无「全部 batch SUCCESS」门禁，水位照推 | **高**：失败 symbol 当日数据永久空洞且无告警；retry 成功后也不 compact | compact 前查 manifest：该 dataset 本 run 有 failed batch 则跳过推水位；retry 收尾自动 compact | 🔴 P0 |
| R-19 | TDX 日线分页无早停：只在页 <800 行时停止，不看日期是否已越过 start | 中：**每日增量也翻每只股票全历史**（~8 页/股，请求量放大约 8 倍），拉长运行时间、加大封禁风险 | 页内最老日期 < start 即 break | 🔴 P0 |
| R-20 | adj_factors 缓存判定 `bars.max > cache.max` 每日必真 → 全市场每日重抓（sina 全局 5 req/s ≈ 18 分钟）；qfq 因子锚定最新价，每日重写全部历史分区；抓取失败静默沿用旧缓存 | 中：性能 + 静默陈旧因子导致复权口径不一致 | 改存 hfq/事件累积比率（append-only），qfq 查询期换算；刷新改为除权日/新股驱动；失败产 finding 而非静默 | 🔴 P1 |
| R-21 | EastMoney 限速为进程内 per-client 实例，未走跨进程 `SourceRateLimiters`；并行组 7 step 叠加 ≈7 req/s | 中：封禁风险 | 各 EM adapter 统一走 `config.rate_limit("eastmoney")` | 🔴 P1 |
| R-22 | HTTP 分页中途失败静默截断仍判 success（datacenter/clist/TDX bars）；EM corporate_actions backfill 只拉第 1 页（5000 行封顶）；akshare 月度宏观仅当发布日=运行日才入库 | **高**：半截数据无告警进湖，与 R-14 同族 | 分页失败→退避重试（用上 `max_retries`）→仍失败抛错判 batch failed；backfill 走分页 helper；宏观改「取最近 N 期 + compact 去重」 | 🔴 P1 |
| R-23 | compact/audit/finalize 依赖缺失，拓扑按字母序：finalize wave 实际执行顺序为 audit → compact → derive_adj_factors | **高**：当日数据永远审计不到；source_diffs 比对的是昨日 curated | audit 声明 `depends_on=["compact","derive_adj_factors"]`；compact 依赖见 R-15 | 🔴 P0 |
| R-24 | curated 分区文件原地覆盖（无 tmp+rename）；写一半崩溃即损坏分区，DuckDB 并发读可读到坏文件 | 中：数据损坏需重建 | 同目录写 tmp 后 `os.replace` 原子替换 | 🔴 P1 |
| R-25 | 全量 eager 读取遍布：reader `load()`、水位 `_max_partition_date`、universe、audit、catalog 均「读整个数据集再过滤」；trading_calendar 按日分区产生 396 个单行小文件 | 中：数据量到全市场十年规模后每次查询/每日 run 都全湖扫描 | 统一 `pl.scan_parquet` + hive 分区裁剪；水位从分区目录名推导；calendar 改单文件或按年分区 | 🔴 P1/P2 |
| R-26 | 配置/CLI 缝隙：`[job.init.phases] names` 键 loader 读不到（读的是 `job.init.names`，静默回退默认值）；CLI 默认 `--config configs/stockdata.example.toml` 与 README「复制为 stockdata.toml」矛盾；`sde compact` 只处理 daily_bars；`sde backfill <dataset>` 不 compact | 低-中：行为与文档预期不符 | 修 loader 键名 + 校验；CLI 默认改 stockdata.toml（缺失时报错提示）；compact/backfill 全数据集化 | 🔴 P1 |

---

## 11. 里程碑

| 阶段 | 交付 | 重点 |
|------|------|------|
| M0 | 脚手架、`sde init`、manifest、Orchestrator 骨架 | 🟢 已完成 |
| M1 | 编排收敛：真依赖解析 + wave 并行 + 全局限速 + 写前 schema 校验 + config 引用校验 + 去 `verify=False` + mock 门控（R-14） | 🟢 已完成（修复 R-01/02/04/09/10/11/14） |
| M2 | 增量与续跑：watermark + 批级 manifest + mootdx 分页全量回填 + corporate_actions/calendar/status 真实化 | 🟢 已完成（遗留 R-16/17/18/19） |
| M3 | HTTP 第二批（capital/valuation/announcement）+ adj_factors 并行化 + dragon_tiger/block_trades | 🟢 已完成（遗留 R-15/20/21/22） |
| M4 | failover/snapshot/source_diffs + 跨源一致性审计 | 🟢 已完成（遗留 R-17/23；「连续 2 周稳定日更」未验证） |
| v1.1 | §4.4 扩展 batch（financial_statement_items、index_constituents、macro、market_breadth 等） | 🟢 step/schema 已完成（产出受 R-15 阻断） |
| **v1.2** | **正确性修复批次（本次评审结论，见 §11.1）** | 修复 R-15–R-26；这是当前最高优先级 |

### 11.1 v1.2 修复计划（2026-07-06 全库架构评审）

评审总结论：**四层湖 + schema 契约 + 自研编排的架构方向仍然正确，不推翻**；但「编排拓扑
（compact/audit 依赖缺失）」「curated 覆盖语义（instruments）」「失败语义（水位推进/静默截断）」
三处属于设计错误而非实现瑕疵，会直接污染下游选股结论，须先于任何新数据集修复。

**P0 数据正确性止血（先做，预计 1–2 天）**

| # | 任务 | 对应风险 | 验收 |
|---|------|----------|------|
| 1 | engine 每个 run 末尾自动追加 compact→audit（或 compact 动态依赖本 run 全部数据集 step；audit 声明 depends_on） | R-15/R-23 | 分组 run 后 curated 有当日分区；audit findings 反映本 run 数据 |
| 2 | corporate_actions daily 主源明确化：EM 按日接口作 daily canonical（ADR-0003 增补「per-dataset 主源」），TDX xdxr 仅 backfill/除权日核对 | R-17 | 默认配置 daily run 不再失败；除权日 rebackfill 触发 |
| 3 | compact 门禁 + 水位保护：dataset 本 run 有 failed batch → 不推该 dataset 水位并产 warning finding；retry 成功后自动 compact | R-18 | 构造部分失败集成测试：水位不推进、retry 后数据补齐 |
| 4 | instruments 合并式 compact（merge + PK 去重，保留退市行）；补 list_date/delist_date 数据源 | R-16 | 人工删一行后重跑，旧 symbol 仍在；delist 过滤生效 |
| 5 | TDX 分页早停（页内最老日期 < start 即停） | R-19 | 日增量单 symbol 请求数 ≤2 页 |

**P1 契约与健壮性（约 1 周）**

| # | 任务 | 对应风险 |
|---|------|----------|
| 6 | 统一 fail-loud：分页失败退避重试（启用 `max_retries`/`retry_backoff_seconds`），仍失败抛错；EM corporate_actions backfill 走分页 helper；区分「自然为空」与「抓取失败」 | R-22 |
| 7 | EM adapter 接入跨进程限速 `config.rate_limit("eastmoney")`，收敛三套限速实现 | R-21 |
| 8 | curated 原子写：tmp + `os.replace` | R-24 |
| 9 | audit 扩展到全部 25 个数据集、全量 PK 检查（lazy scan）；macro 月度指标改「最近 N 期 + 去重」 | R-25/R-22 |
| 10 | 配置/CLI 修缝：`job.init.phases.names` 键、CLI 默认 config、`sde compact`/`backfill` 全数据集化、retry_count 累计 | R-26/R-03 |

**P2 性能与口径（约 1 周，可与 P1 并行）**

| # | 任务 | 对应风险 |
|---|------|----------|
| 11 | 消费层与内部读取统一 `pl.scan_parquet` + hive 分区裁剪；水位从分区目录名推导 | R-25 |
| 12 | adj_factors 改 append-only（存 hfq/事件比率，qfq 查询期换算）；刷新改除权日驱动；失败产 finding | R-20 |
| 13 | trading_calendar 改单文件/按年分区；snapshot 目录按保留期清理 | R-25/§6.4 |
| 14 | market_breadth 涨跌停阈值按板块区分（主板 10%/创业板科创板 20%/北交所 30%/ST 5%），当前统一 9.5% 的口径在文档中显式披露 | 数据口径 |

**P3 防回归**：分组 run→curated 端到端断言、部分失败水位测试、audit 顺序测试、集成测试
把 `status in ("success","failed")` 这类恒真断言改为精确断言。

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

---

## 附录 A：Schema 契约

StockDataEngine curated datasets share provenance columns and explicit primary keys.

### Global conventions

| Rule | Value |
|------|-------|
| Timezone | `Asia/Shanghai` for all `trade_date` and business timestamps |
| Symbol | `{code}.{SH\|SZ\|BJ}` e.g. `600519.SH` |
| Exchange column | `SH`, `SZ`, or `BJ` |
| Provenance columns | `source`, `data_version`, `fetched_at` (UTC timestamp) on every curated row |
| Null semantics | Suspended days: OHLCV present, `volume=0`, `amount=0` |
| Schema evolution | Additive columns only; breaking changes bump `dataset_schema_version` |

### Partition keys (curated)

| Dataset | Partition |
|---------|-----------|
| daily_bars | `trade_date` |
| index_bars | `trade_date` |
| minute_bars | `frequency`, `trade_date`, `symbol_bucket` |
| trading_status | `trade_date` |
| corporate_actions | `ex_date` (year-month) |
| adj_factors | `trade_date` |
| financial_statement_items | `report_period` |
| industry_members | `as_of_date` |
| northbound_flows | `trade_date` |

Multi-source snapshots: `meta/source_snapshots/{dataset}/source={source}/data_version={ver}/`

### Primary keys

| Dataset | Primary key |
|---------|-------------|
| instruments | `(symbol)` |
| trading_calendar | `(trade_date)` |
| trading_status | `(symbol, trade_date)` |
| daily_bars | `(symbol, trade_date)` |
| index_bars | `(symbol, trade_date, frequency)` |
| minute_bars | `(symbol, trade_date, bar_time, frequency)` |
| corporate_actions | `(symbol, ex_date, action_type)` |
| adj_factors | `(symbol, trade_date, adjust_type)` |
| fund_flow | `(symbol, trade_date)` |
| northbound_holdings | `(symbol, trade_date, channel)` |
| northbound_flows | `(trade_date, channel)` |
| margin_trading | `(symbol, trade_date)` |
| sector_members | `(symbol, sector_code, as_of_date)` |
| valuation_metrics | `(symbol, trade_date)` |
| announcement_index | `(announcement_id)` |
| financial_statement_items | `(symbol, report_period, statement_type, item_code)` |
| industry_members | `(symbol, classification_system, as_of_date)` |

### MVP-P0 column definitions

#### instruments

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | PK |
| name | string | |
| exchange | string | SH/SZ/BJ |
| asset_type | string | stock/etf/index |
| list_date | date | nullable |
| delist_date | date | nullable |
| prev_symbol | string | nullable |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### trading_calendar

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | PK |
| is_trading | bool | |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### trading_status

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| is_trading | bool | |
| status | string | normal/suspended/st/*st |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### daily_bars

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| open | float64 | unadjusted |
| high | float64 | |
| low | float64 | |
| close | float64 | |
| volume | int64 | shares |
| amount | float64 | CNY |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### index_bars

Same as daily_bars plus `frequency` (default `1d`), `asset_type=index`.

#### corporate_actions

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| ex_date | date | |
| action_type | string | cash_dividend/bonus/transfer/allotment |
| cash_dividend | float64 | per share |
| bonus_ratio | float64 | per 10 shares |
| transfer_ratio | float64 | per 10 shares |
| allotment_ratio | float64 | nullable |
| allotment_price | float64 | nullable |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### adj_factors

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| adjust_type | string | qfq/hfq |
| factor | float64 | cumulative factor; qfq: `1/sina_qfq_factor`, hfq: `sina_hfq_factor` |
| source | string | sina (default) |
| data_version | string | |
| fetched_at | timestamp | |

#### financial_statement_items

Point-in-time (PIT) queries **must** filter on `announce_date <= as_of` at read time
(`load(..., as_of=)`); never align fundamentals by `report_period` alone.

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| report_period | string | e.g. ``2024Q1`` |
| statement_type | string | income / balance / cashflow |
| item_code | string | e.g. ``roe``, ``revenue`` |
| item_value | float64 | |
| announce_date | date | **PIT axis** — public disclosure date |
| source | string | |
| data_version | string | |
| fetched_at | timestamp | |

#### fund_flow

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| main_net_inflow | float64 | CNY |
| super_large_net_inflow | float64 | |
| large_net_inflow | float64 | |
| medium_net_inflow | float64 | |
| small_net_inflow | float64 | |
| source / data_version / fetched_at | | provenance |

#### margin_trading

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| margin_balance | float64 | |
| margin_buy | float64 | |
| short_balance | float64 | |
| short_sell_volume | float64 | |
| source / data_version / fetched_at | | provenance |

#### northbound_holdings

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| channel | string | SH / SZ connect |
| holding_shares | float64 | |
| holding_mv | float64 | |
| holding_ratio | float64 | |
| source / data_version / fetched_at | | provenance |

#### northbound_flows

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | |
| channel | string | SH / SZ |
| net_buy | float64 | |
| buy_amount | float64 | |
| sell_amount | float64 | |
| source / data_version / fetched_at | | provenance |

#### valuation_metrics

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| pe_ttm | float64 | |
| pb | float64 | |
| ps_ttm | float64 | |
| total_mv | float64 | |
| float_mv | float64 | |
| source / data_version / fetched_at | | provenance |

#### sector_members

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| sector_code | string | |
| sector_name | string | |
| as_of_date | date | snapshot date |
| source / data_version / fetched_at | | provenance |

#### announcement_index

PIT queries filter `announce_date <= as_of`.

| Column | Type | Notes |
|--------|------|-------|
| announcement_id | string | PK |
| symbol | string | |
| title | string | |
| announce_date | date | **PIT axis** |
| category | string | |
| url | string | |
| source / data_version / fetched_at | | provenance |

#### dragon_tiger

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| reason | string | |
| buy_amount | float64 | |
| sell_amount | float64 | |
| net_amount | float64 | |
| source / data_version / fetched_at | | provenance |

#### block_trades

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| price | float64 | |
| volume | float64 | |
| amount | float64 | |
| premium_ratio | float64 | discount vs close |
| source / data_version / fetched_at | | provenance |

#### index_constituents

| Column | Type | Notes |
|--------|------|-------|
| index_symbol | string | e.g. ``000300.SH`` |
| symbol | string | constituent |
| as_of_date | date | snapshot / rebalance date |
| weight | float64 | index weight (percent or ratio per source) |
| source / data_version / fetched_at | | provenance |

#### industry_members

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| classification_system | string | e.g. ``sw``, ``eastmoney`` |
| industry_code | string | |
| industry_name | string | |
| as_of_date | date | classification snapshot |
| source / data_version / fetched_at | | provenance |

#### macro_indicators

| Column | Type | Notes |
|--------|------|-------|
| indicator_id | string | e.g. ``shibor_3m``, ``cnbond_yield_10y``, ``lpr_1y`` |
| obs_date | date | observation / release date |
| value | float64 | |
| frequency | string | ``daily`` / ``monthly`` |
| source / data_version / fetched_at | | provenance |

#### market_breadth

Computed from curated ``daily_bars`` vs prior trading day.

| Column | Type | Notes |
|--------|------|-------|
| trade_date | date | |
| metric_id | string | ``advance_count``, ``decline_count``, ``limit_up_count``, … |
| value | float64 | |
| source / data_version / fetched_at | | provenance |

#### share_unlock_schedule

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| unlock_date | date | scheduled unlock |
| unlock_shares | float64 | |
| unlock_ratio | float64 | fraction of float/total per source |
| unlock_type | string | e.g. IPO lock-up, private placement |
| source / data_version / fetched_at | | provenance |

#### regulatory_events

| Column | Type | Notes |
|--------|------|-------|
| event_id | string | PK |
| symbol | string | |
| event_date | date | announcement date |
| event_type | string | ``penalty``, ``investigation``, ``regulatory_letter``, … |
| title | string | |
| source / data_version / fetched_at | | provenance |

#### institutional_holdings

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| holder_type | string | ``fund``, ``qfii``, ``social_security``, … |
| report_period | string | e.g. ``2024Q1`` |
| holding_shares | float64 | holder count or share volume per source |
| holding_ratio | float64 | pct of float/total |
| holding_mv | float64 | market value |
| source / data_version / fetched_at | | provenance |

#### analyst_consensus

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| forecast_date | date | publish / update date |
| forecast_year | int64 | target fiscal year |
| eps_forecast | float64 | consensus EPS |
| pe_forecast | float64 | implied PE |
| target_price | float64 | avg target price |
| rating | string | e.g. 买入/增持 |
| analyst_count | int64 | covering institutions |
| source / data_version / fetched_at | | provenance |

#### sentiment_scores

Dual channels: ``announcement_keywords`` (公告标题) and ``stock_news_nlp`` (EastMoney 个股新闻 + keyword/SnowNLP).

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | |
| trade_date | date | |
| score_channel | string | PK axis; ``announcement_keywords`` / ``stock_news_nlp`` |
| sentiment_score | float64 | [-1, 1] |
| headline_count | int64 | headlines scored |
| source / data_version / fetched_at | | provenance |

#### stock_news (on-demand cache)

Cached JSON at ``meta/on_demand/stock_news/{symbol}.json``; fetched via ``sde query --dataset stock_news --symbol``.

| Field | Type | Notes |
|-------|------|-------|
| symbol | string | |
| items[].news_id | string | |
| items[].title | string | |
| items[].publish_time | string | |
| items[].publish_date | string | ISO date when parseable |
| items[].sentiment_score | float64 | per-headline NLP score |
| items[].sentiment_method | string | ``keyword`` / ``snownlp`` / ``keyword+snownlp`` |
| aggregate_sentiment | float64 | mean of item scores |
| headline_count | int64 | |
| source / data_version / fetched_at | | provenance |

### Compact deduplication

On compact: group by primary key, keep row with max(`fetched_at`).

### DuckDB views

```sql
CREATE VIEW daily_bars_view AS
SELECT * FROM read_parquet('{root}/curated/daily_bars/**/*.parquet', hive_partitioning=true);

CREATE VIEW daily_bars_adj AS
SELECT b.*, b.close * a.factor AS adj_close
FROM daily_bars_view b
LEFT JOIN read_parquet('{root}/derived/adj_factors/**/*.parquet', hive_partitioning=true) a
  ON b.symbol = a.symbol AND b.trade_date = a.trade_date AND a.adjust_type = 'qfq';
```

---

## 附录 B：数据集目录（逐源限制与更新频率）

Per-dataset source, update frequency, and known limitations (ashare-data-warehouse style).

### Legend

- **Wave:** daily batch step name
- **On-demand:** fetched by `OnDemandService` on first query

---

### MVP-P0

#### instruments

| Item | Value |
|------|-------|
| Wave | `instruments` (Wave 0) |
| Primary source | tdx_protocol (mootdx security_list) |
| Backup | akshare |
| Frequency | daily |
| PK | symbol |
| Universe | SH/SZ/BJ prefix whitelist 60/68/00/30/92 |
| Known limits | list_date may be null from TDX |

#### trading_calendar

| Item | Value |
|------|-------|
| Wave | `trading_calendar` (Wave 0) |
| Primary source | tdx_protocol |
| Backup | exchange CSV |
| Frequency | yearly refresh + daily check |
| PK | trade_date |

#### trading_status

| Item | Value |
|------|-------|
| Wave | `trading_status` (Wave 0) |
| Primary source | tdx_protocol |
| Backup | eastmoney |
| Frequency | daily |
| PK | (symbol, trade_date) |

#### daily_bars

| Item | Value |
|------|-------|
| Wave | `daily_bars` (Wave 1, after corporate_actions) |
| Primary source | tdx_protocol (unadjusted) |
| Backup | eastmoney |
| Frequency | daily incremental; full backfill on init Phase 2c |
| PK | (symbol, trade_date) |
| Rebackfill | symbols from corporate_actions same-day ex_date |
| Known limits | TDX rate limit; use ≤8 workers |

#### index_bars

| Item | Value |
|------|-------|
| Wave | `index_bars` (Wave 2) |
| Primary source | tdx_protocol |
| Backup | eastmoney |
| Frequency | daily |
| PK | (symbol, trade_date, frequency) |

#### corporate_actions

| Item | Value |
|------|-------|
| Wave | `corporate_actions` (Wave 1, before daily_bars) |
| Primary source | tdx_protocol ex_rights |
| Backup | eastmoney datacenter |
| Frequency | daily |
| PK | (symbol, ex_date, action_type) |
| Output | `symbols_to_rebackfill` manifest metadata |

#### adj_factors (derived)

| Item | Value |
|------|-------|
| Step | `derive_adj_factors` (Wave finalize) |
| Primary source | sina (qfq/hfq factor series) |
| Input | daily_bars trade dates + external factor API |
| Frequency | daily after compact |
| PK | (symbol, trade_date, adjust_type) |
| Note | External cumulative factors aligned to daily_bars; `adj_close = close * factor` |

---

### v1.0-full (batch 2)

#### fund_flow

| Item | Value |
|------|-------|
| Group | core@16:30 |
| Primary source | eastmoney |
| PK | (symbol, trade_date) |

#### northbound_holdings / northbound_flows

| Item | Value |
|------|-------|
| Group | capital@16:30 |
| Primary source | eastmoney |
| PK | 见附录 A |

#### margin_trading

| Item | Value |
|------|-------|
| Group | signals@17:00 |
| Primary source | eastmoney / akshare |
| PK | (symbol, trade_date) |

#### valuation_metrics

| Item | Value |
|------|-------|
| Primary source | eastmoney |
| Backup | tencent |
| PK | (symbol, trade_date) |

#### announcement_index

| Item | Value |
|------|-------|
| Primary source | cninfo |
| PK | announcement_id |
| Note | Full text via on-demand `announcement_body` |

---

### On-demand datasets

Not in daily Wave. Cached under `meta/on_demand/` and optional DuckDB tables.

| Dataset | Source | Trigger |
|---------|--------|---------|
| announcement_body | cninfo | `sde query --dataset announcement_body --symbol` |
| stock_news | eastmoney / akshare | per symbol |
| research_reports | eastmoney reportapi | per symbol |
| financial_reports | sina / gpcw | per symbol |

---

### Meta datasets

| Dataset | Storage |
|---------|---------|
| ingestion_runs | manifest.db |
| ingestion_batches | manifest.db |
| quality_findings | meta/quality/findings/ |
| source_diffs | meta/quality/source_diffs/ |
| data_catalog | generated by `sde catalog` |

---

### Source availability matrix

| Source | Protocol | MVP usage | Backup | Degrade |
|--------|----------|-----------|--------|---------|
| tdx_protocol | TCP | bars, instruments, calendar | eastmoney | audit alert only |
| sina | HTTP | adj_factors (qfq/hfq) | — | skip symbol + quality finding |
| eastmoney | HTTP | corp actions backup, capital | akshare | skip + quality finding |
| cninfo | HTTP | announcement_index | — | on-demand only |
| akshare | HTTP | optional | — | disabled by default |

调度与 failover 运维见附录 C。

---

## 附录 C：运维手册

### Directory layout

After `sde init`:

```
{data.root}/
  staging/
  curated/
  derived/
  meta/manifest.db
  meta/quality/
  meta/source_snapshots/
  meta/on_demand/
  duckdb/stockdata.duckdb
```

### T+1 daily schedule (cron example)

> ⚠️ **R-15 修复前，分组模式（`--group`）产出不会进入 curated**（组内 compact 缺失或
> 空跑）。修复上线前，生产日更请使用不带 `--group` 的完整 Wave 模式
> `sde run daily --config configs/stockdata.toml`（其 finalize wave 会 compact，
> 但注意 audit 顺序缺陷 R-23）。以下为修复后的目标态：

```cron
# Core reference + bars + derive (Mon-Fri 16:05)
5 16 * * 1-5 cd /path/to/StockDataEngine && sde run daily --group core --config configs/stockdata.toml

# Capital tables (16:35)
35 16 * * 1-5 sde run daily --group capital --config configs/stockdata.toml

# Signals (17:05)
5 17 * * 1-5 sde run daily --group signals --config configs/stockdata.toml
```

### Init phases

```bash
sde init --config configs/stockdata.example.toml
```

Runs phases in order; Phase 2c (daily_bars backfill) may take 15–20 minutes.

### Failure recovery

```bash
sde status --config configs/stockdata.example.toml
sde retry --run-id <id> --config configs/stockdata.example.toml
```

Only failed batches are re-executed; successful batches are skipped.

### Audit

```bash
sde audit --config configs/stockdata.example.toml
```

Writes findings to `meta/quality/findings/{run_id}.json`. Cross-source diffs go to `meta/quality/source_diffs/`. **No automatic source switching.**

### Backup

```bash
tar czf backup-$(date +%Y%m%d).tar.gz data/stock-data-engine/curated data/stock-data-engine/meta
cp data/stock-data-engine/duckdb/stockdata.duckdb backup/
```

### Source failover policy

1. Primary source fails → batch retry with backoff (max 3).
2. Still failing → mark batch failed; optional backup fetch writes to `meta/source_snapshots`.
3. `sde audit` compares primary vs snapshot; human decides source switch.
4. Never silently overwrite curated canonical rows from backup.

### EastMoney HTTP

Engine applies NID auth patch at startup (`adapters/eastmoney/em_auth.py`). Ensure outbound HTTPS to `*.eastmoney.com`.

### Docker (optional)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[tdx]"
CMD ["sde", "run", "daily", "--config", "configs/stockdata.example.toml"]
```

Mount `{data.root}` as a volume for persistence.

### Monitoring

- `sde status`: latest run, batch counts, failed batches
- manifest.db tables: `ingestion_runs`, `ingestion_batches`
- Optional: export `.progress.json` from status for external dashboards
