# StockDataEngine 文档

本地部署的 A 股选股数据层：多源采集、编排、标准化，交付带溯源、列契约稳定的 Parquet 湖。

CLI 是 `sde`，Python 包是 `stock_data_engine`。默认从 `configs/stockdata.example.toml` 复制出本地 `stockdata.toml`；数据湖根目录默认 `./data/stock-data-engine`。

开源读者建议顺序：[与同类项目差异](comparison.md) → [许可与数据合规](legal-and-data-sources.md) → [安装](getting-started/installation.md)。

## 定位与合规

- [与同类项目差异](comparison.md)
- [许可与数据合规](legal-and-data-sources.md)
- [SECURITY](../SECURITY.md)（漏洞私下报告）

## 入门

- [安装](getting-started/installation.md)
- [快速开始](getting-started/quickstart.md)
- [配置参考](getting-started/configuration.md)

## 架构

- [架构总览](architecture/overview.md) · [数据流](architecture/data-flow.md) · [数据湖布局](architecture/lake-layout.md)
- [设计原则](architecture/design-principles.md) · [架构设计（完整版）](architecture.md)
- [ADR](adr/)

## 模块与数据集

模块说明按源码包拆在 [modules/](modules/README.md)（config / domain / adapters / orchestrator / steps / storage / derive / quality / query / cli）。

数据集：

- [总览](datasets/README.md) · [目录](datasets/catalog.md) · [Schema](datasets/schema.md)
- [查询指南](datasets/query-guide.md) · [逐源限制](datasets/sources.md)

## 运维与开发

- [Runbook](operations/runbook.md) · [脚本说明](operations/scripts.md) · [故障排查](operations/troubleshooting.md)
- [开发约定](development/conventions.md) · [测试](development/testing.md) · [新增数据集](development/adding-dataset.md)
- [CONTRIBUTING](../CONTRIBUTING.md)

## 参考

- [CLI](reference/cli.md) · [Python API](reference/python-api.md) · [CHANGELOG](../CHANGELOG.md)
