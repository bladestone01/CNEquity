# StockDataEngine 文档中心

本地部署的 A 股选股数据层：多源采集、编排、标准化，交付带溯源、列契约稳定的 Parquet 数据湖。

| 入口 | 说明 |
|------|------|
| CLI | `sde` |
| Python 包 | `stock_data_engine` |
| 默认配置 | `configs/stockdata.toml`（从 `stockdata.example.toml` 复制） |
| 数据湖根目录 | `{data.root}`，默认 `./data/stock-data-engine` |
| 仓库 | https://github.com/rootSunc/stock-data-engine |

开源读者建议先读：[与同类项目差异](comparison.md) → [许可与数据合规](legal-and-data-sources.md) → [安装](getting-started/installation.md)。

---

## 文档地图

### 定位与合规

| 文档 | 内容 |
|------|------|
| [与同类项目差异](comparison.md) | 相对 AkShare / Tushare / Baostock / Qlib 等的边界与选型 |
| [许可与数据合规](legal-and-data-sources.md) | MIT 覆盖代码；上游数据条款与用户责任 |
| [SECURITY](../SECURITY.md) | 漏洞私下报告 |
| [行为准则](../CODE_OF_CONDUCT.md) | Contributor Covenant |

### 入门

| 文档 | 内容 |
|------|------|
| [安装](getting-started/installation.md) | 环境、依赖、可选 extra |
| [快速开始](getting-started/quickstart.md) | init → daily → 读数据 |
| [配置参考](getting-started/configuration.md) | `stockdata.toml` 全量键说明 |

### 架构

| 文档 | 内容 |
|------|------|
| [架构总览](architecture/overview.md) | 六层设计、模块边界、与 Workbench 契约 |
| [数据流](architecture/data-flow.md) | init / daily / compact / audit / retry |
| [数据湖布局](architecture/lake-layout.md) | staging / curated / derived / meta 目录契约 |
| [设计原则](architecture/design-principles.md) | 可信原则、ADR 摘要、演进约束 |
| [架构设计（完整版）](architecture.md) | 差距分析 G1–G7、实盘可信度镜头 |
| [ADR 目录](adr/) | 架构决策记录 |

### 模块（按源码包）

| 文档 | 对应目录 |
|------|----------|
| [模块索引](modules/README.md) | `src/stock_data_engine/` 总览 |
| [config](modules/config.md) | 配置加载与校验 |
| [domain](modules/domain.md) | Schema、数据集注册表、符号规则 |
| [adapters](modules/adapters/README.md) | 各数据源适配器 |
| [orchestrator](modules/orchestrator.md) | 引擎、manifest、worker pool |
| [steps](modules/steps.md) | 采集步骤与 Wave DAG |
| [storage](modules/storage.md) | Parquet 读写、compact、水位 |
| [derive](modules/derive.md) | 派生数据集 |
| [quality](modules/quality.md) | 审计、跨源 diff、failover |
| [query](modules/query.md) | `load()` API、DuckDB 视图 |
| [cli](modules/cli.md) | CLI 实现与退出码 |

### 数据集

| 文档 | 内容 |
|------|------|
| [数据集总览](datasets/README.md) | L0–L8 分层、模式（batch/on-demand/derived） |
| [数据集目录](datasets/catalog.md) | 全量表：PK、分区、主源、语义 |
| [查询指南](datasets/query-guide.md) | 复权、Universe、PIT、strict_adj |
| [Schema 契约（权威）](PRD.md#附录-a-schema-契约) | PRD 附录 A 字段级定义 |
| [数据源限制（权威）](PRD.md#附录-b-数据集目录与数据源) | PRD 附录 B 逐源说明 |

### 运维

| 文档 | 内容 |
|------|------|
| [运维 Runbook](operations/runbook.md) | 调度、SLO、备份恢复 |
| [脚本说明](operations/scripts.md) | `scripts/` 各脚本用途 |
| [故障排查](operations/troubleshooting.md) | 常见问题与处置流程 |

### 开发

| 文档 | 内容 |
|------|------|
| [开发约定](development/conventions.md) | 包结构、分层、代码风格 |
| [测试](development/testing.md) | pytest 结构、标记、离线原则 |
| [新增数据集](development/adding-dataset.md) | Definition of Done 清单 |
| [CONTRIBUTING](../CONTRIBUTING.md) | 贡献流程速查 |

### 参考手册

| 文档 | 内容 |
|------|------|
| [CLI 参考](reference/cli.md) | 全部 `sde` 子命令与选项 |
| [Python API 参考](reference/python-api.md) | `load` / `scan` / `list_datasets` |
| [产品需求（PRD）](PRD.md) | 需求、风险登记、运维附录 C |
| [路线图](roadmap.md) | Phase A–D 排期 |
| [CHANGELOG](../CHANGELOG.md) | 版本变更 |

---

## 按角色阅读路径

**量化研究员（只用数据）**

1. [快速开始](getting-started/quickstart.md) → [查询指南](datasets/query-guide.md) → [Python API](reference/python-api.md)

**数据工程师（运维日更）**

1. [配置参考](getting-started/configuration.md) → [运维 Runbook](operations/runbook.md) → [CLI 参考](reference/cli.md) → [故障排查](operations/troubleshooting.md)

**贡献者（加数据集/改引擎）**

1. [架构总览](architecture/overview.md) → [模块索引](modules/README.md) → [新增数据集](development/adding-dataset.md) → [PRD 风险登记](PRD.md#10-风险登记册)

---

## 状态图例

文档中与 PRD 一致的状态标注：

- ✅ / 🟢 — 已实现可用
- 🟡 — 部分实现或已知限制
- 🔜 / 🔴 — 规划中或未实现
