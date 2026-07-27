# ashare-lake 文档

本地部署的 A 股选股数据层：多源采集、编排、标准化，交付带溯源、列契约稳定的 Parquet 湖。

CLI 是 `asl`，Python 包是 `ashare_lake`。推荐：`pip install "ashare-lake[tdx]"` → `asl demo` 试玩，或 `asl config init` 写出 `configs/ashare-lake.toml` 再跑全量。数据湖根目录默认 `./data/ashare-lake`。

开源读者建议顺序：[与同类项目差异](comparison.md) → [许可与数据合规](legal-and-data-sources.md) → [安装](getting-started/installation.md) → [快速开始](getting-started/quickstart.md)。卡在网络 / TDX / workers 时看 [排障](operations/troubleshooting.md)。

面向使用者的文档以中文为准；[CHANGELOG](../CHANGELOG.md) 与 [ADR](adr/) 保持英文（技术决策与版本记录）。英文简介见 [README.en.md](../README.en.md)。

### 术语约定

| 中文 | 英文/代码 | 含义 |
|------|-----------|------|
| 波次 | Wave | 日更编排中的一批并行/串行 step |
| 主键 | PK / primary key | curated 去重与契约键 |
| 主源 / 备源 | primary / backup | 写入 curated 的源 vs 仅进 source_snapshots 的源 |
| 主备切换 | Failover | 主源失败时写备源快照，不自动顶替 curated |
| 权威行 | canonical | curated 中每主键一行的正式数据 |
| 溯源 | provenance | `source` / `data_version` / `fetched_at` |
| 水位 | watermark | `meta/state` 中已成功 compact 的进度 |
| 按需 | on-demand | 不进日更主路径、按 symbol 拉取并缓存 |
| 备份 | backup（脚本/目录） | 元数据归档；与「备源」不同 |

## 定位与合规

- [与同类项目差异](comparison.md)
- [许可与数据合规](legal-and-data-sources.md)
- [安全策略](../SECURITY.md)（漏洞私下报告）

## 入门

- [安装](getting-started/installation.md)
- [快速开始](getting-started/quickstart.md)
- [配置参考](getting-started/configuration.md)

## 架构

- [架构总览](architecture/overview.md) · [数据流](architecture/data-flow.md) · [数据湖布局](architecture/lake-layout.md)
- [设计原则](architecture/design-principles.md) · [架构设计（完整版）](architecture.md)
- [ADR](adr/)（英文）

## 模块与数据集

模块说明按源码包拆在 [modules/](modules/README.md)（config / domain / adapters / orchestrator / steps / storage / derive / quality / query / cli）。

数据集：

- [总览](datasets/README.md) · [目录](datasets/catalog.md) · [Schema](datasets/schema.md)
- [查询指南](datasets/query-guide.md) · [逐源限制](datasets/sources.md)

## 运维与开发

- [Runbook](operations/runbook.md) · [脚本说明](operations/scripts.md) · [故障排查](operations/troubleshooting.md)
- [开发约定](development/conventions.md) · [测试](development/testing.md) · [新增数据集](development/adding-dataset.md)
- [贡献指南](../CONTRIBUTING.md)

## 参考

- [CLI](reference/cli.md) · [Python API](reference/python-api.md) · [CHANGELOG](../CHANGELOG.md)
