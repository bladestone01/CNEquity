# 与同类项目的差异

一句话：**StockDataEngine 是本地部署的 A 股「数据层」**——多源采集 + 编排 + 契约化 Parquet 湖 + 可审计查询；不是行情 SDK 合集，也不是回测/交易框架。

## 快速对照

| | StockDataEngine | AkShare / efinance 等 | Tushare Pro | Baostock | Qlib / vn.py 等 |
|--|-----------------|------------------------|-------------|----------|-----------------|
| 定位 | 自建选股数据湖 + 日更编排 | 拉数函数库 | 云端积分 API | 免费会话 API | 研究/交易平台（含数据子系统） |
| 交付物 | curated Parquet + DuckDB 视图 + `load()` | 内存 DataFrame | 远端表/CSV | DataFrame | 平台内数据集或行情接入 |
| 编排 | `init` / `daily` / `retry`、manifest、水位 | 无（调用方自管） | 无 | 无 | 各平台自有 |
| Schema / 溯源 | 写前校验；`source` / `data_version` / `fetched_at` | 通常无统一契约 | 平台字段 | 字段固定但无湖契约 | 视模块而定 |
| 多源策略 | 主源 canonical；备源进 snapshot；**永不自动切源** | 单源调用 | 单平台 | 单源 | 视配置 |
| 历史回填 | 分页、checkpoint、按数据集 backfill | 脚本循环调用 | 积分与权限 | 按接口能力 | 视方案 |
| 查询口径 | 复权组合、universe、PIT `as_of` | 自己拼 | 自己拼 | 自己拼 | 平台口径 |
| 质量 | audit、跨源 diff、fail-loud | 无 | 平台侧 | 有限 | 视模块 |
| 部署 | 本地（或自有机器）；数据不出境除非你导出 | 本地调 HTTP | 依赖云账号 | 本地调官方 | 本地/集群 |
| 许可边界 | 代码 MIT；**数据条款见** [legal](legal-and-data-sources.md) | 各库 + 上游 | 商业/积分协议 | 官方协议 | 各项目许可 |

## 本项目坚持的差异点

### 1. 湖，而不是「又一次 `get_xxx()`」

同类库解决「怎么把网页/API 变成 DataFrame」。本项目解决：

- 全市场如何 **幂等落盘**（staging → compact → curated）
- 日更如何 **只抓水位之后**、失败如何 **按 batch 续跑**
- 下游如何 **稳定依赖列名与主键**，而不是每次脚本列名漂移

### 2. 可信契约优先于覆盖面

- **永不伪造**：源失败即 batch 失败；mock 仅测试门控且强制标记。
- **可溯源**：curated 行带 provenance 列。
- **无前视**：财报等支持 `announce_date` + `load(..., as_of=)`。
- **多源不打架**：备源可审计比对，不静默覆盖主源（ADR-0003）。

覆盖面可以后补；会污染下游选股结论的口径问题优先修。

### 3. 编排与运维是一等公民

`sde init` / `run` / `retry` / `audit` / `status`、分组调度、限速、manifest WAL、验收脚本——这些在「纯 adapter 库」里通常缺失，却是本地数据湖能否跑过两周的关键。

### 4. 明确不做的事

| 不做 | 原因 |
|------|------|
| 回测、信号、下单 | 下游选股/策略项目的职责 |
| 托管云行情或出售数据文件 | 合规与定位都不匹配 |
| 自动把备源写成 canonical | 避免静默口径漂移 |
| 保证上游 ToS 下的商用再分发 | 见 [legal-and-data-sources.md](legal-and-data-sources.md) |

## 什么时候选本项目

**适合**

- 需要 **2016+ 可回填** 的本地 Parquet 湖，供多个选股/因子仓库共用
- 在意 ST/停牌 universe、复权、PIT 等 **口径可复现**
- 希望日更失败可 **定位到 batch 并 retry**，而不是整段脚本重跑
- 能接受自备机器、磁盘与大陆可达网络（部分源对出口敏感）

**不太适合**

- 只想在 notebook 里临时 `import` 拉一张表 → 用 AkShare / Baostock 更轻
- 只要云端宽表、不愿运维湖 → Tushare 等更合适
- 要的是完整交易/研究 IDE → Qlib、vn.py 等平台，而非本仓库

## 与本仓库文档的衔接

| 你想了解 | 去读 |
|----------|------|
| 分层数据集与字段 | [datasets/catalog.md](datasets/catalog.md)、[schema.md](datasets/schema.md) |
| 安装与日更 | [getting-started/](getting-started/installation.md) |
| 架构与 ADR | [architecture/overview.md](architecture/overview.md)、[adr/](adr/) |
| 合规 | [legal-and-data-sources.md](legal-and-data-sources.md) |
