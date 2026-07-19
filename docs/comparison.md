# 与同类项目的差异

StockDataEngine 是本地部署的 A 股数据层：多源采集 + 编排 + 契约化 Parquet 湖 + 可审计查询。不是行情 SDK 合集，也不是回测 / 交易框架。

## 快速对照

| | StockDataEngine | AkShare / efinance 等 | Tushare Pro | Baostock | Qlib / vn.py 等 |
|--|-----------------|------------------------|-------------|----------|-----------------|
| 定位 | 自建选股数据湖 + 日更编排 | 拉数函数库 | 云端积分 API | 免费会话 API | 研究/交易平台（含数据子系统） |
| 交付物 | curated Parquet + DuckDB 视图 + `load()` | 内存 DataFrame | 远端表/CSV | DataFrame | 平台内数据集或行情接入 |
| 编排 | `init` / `daily` / `retry`、manifest、水位 | 无（调用方自管） | 无 | 无 | 各平台自有 |
| Schema / 溯源 | 写前校验；`source` / `data_version` / `fetched_at` | 通常无统一契约 | 平台字段 | 字段固定但无湖契约 | 视模块而定 |
| 多源策略 | 主源 canonical；备源进 snapshot，不自动切源 | 单源调用 | 单平台 | 单源 | 视配置 |
| 历史回填 | 分页、checkpoint、按数据集 backfill | 脚本循环调用 | 积分与权限 | 按接口能力 | 视方案 |
| 查询口径 | 复权组合、universe、PIT `as_of` | 自己拼 | 自己拼 | 自己拼 | 平台口径 |
| 质量 | audit、跨源 diff、源失败即暴露 | 无 | 平台侧 | 有限 | 视模块 |
| 部署 | 本地（或自有机器） | 本地调 HTTP | 依赖云账号 | 本地调官方 | 本地/集群 |
| 许可边界 | 代码 MIT；数据条款见 [legal](legal-and-data-sources.md) | 各库 + 上游 | 商业/积分协议 | 官方协议 | 各项目许可 |

## 实际差在哪

同类库解决「怎么把网页 / API 变成 DataFrame」。这边多管几件事：全市场怎么幂等落盘（staging → compact → curated）、日更怎么只抓水位之后、失败怎么按 batch 续跑、下游怎么稳定依赖列名与主键。

口径上几条硬约束：源失败就让 batch 失败（mock 仅测试门控且强制标记）；curated 行带 provenance；财报支持 `announce_date` + `load(..., as_of=)`；备源可审计比对，不静默覆盖主源（见 [ADR-0003](adr/0003-canonical-curated-with-source-snapshots.md)）。覆盖面可以后补，会污染下游结论的口径问题优先修。

编排也算一等公民：`sde init` / `run` / `retry` / `audit` / `status`、分组调度、限速、manifest WAL、验收脚本——纯 adapter 库里通常没有，本地湖要跑过两周却离不开。

明确不做：回测、信号、下单；托管云行情或出售数据文件；自动把备源写成 canonical；保证上游 ToS 下的商用再分发（见 [legal](legal-and-data-sources.md)）。

## 什么时候选本项目

适合的情况：需要 2016+ 可回填的本地 Parquet 湖，给多个选股 / 因子仓库共用；在意 ST / 停牌 universe、复权、PIT 等口径可复现；希望日更失败能定位到 batch 并 retry。能接受自备机器、磁盘，以及部分源对出口敏感的现实。

不太适合：只想在 notebook 里临时拉一张表（AkShare / Baostock 更轻）；只要云端宽表、不愿运维湖（Tushare 等）；要的是完整交易 / 研究 IDE（Qlib、vn.py）。

## 相关文档

分层数据集与字段：[catalog](datasets/catalog.md)、[schema](datasets/schema.md)。安装与日更：[getting-started](getting-started/installation.md)。架构与决策：[overview](architecture/overview.md)、[adr](adr/)。合规：[legal](legal-and-data-sources.md)。
