# 贡献指南

安全问题请走 [SECURITY.md](SECURITY.md)，不要开公开 issue。

提较大功能前，请先看 [定位与差异](docs/comparison.md)（本仓库只做数据层）和
[许可与数据合规](docs/legal-and-data-sources.md)。

## 环境

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx,dev]"
# 可选 extras：valuation / macro / nlp / structure
# 见 docs/getting-started/installation.md
```

请勿提交 `configs/ashare-lake.toml`、`data/`、`logs/`。

```bash
ruff format .
ruff check .
pytest                 # 全部
pytest tests/unit      # 快速
pytest tests/integration
```

## 约定

- 代码在 `src/ashare_lake/`，按职责拆分（`domain`、`adapters`、`orchestrator`、
  `steps`、`storage`、`derive`、`quality`、`query`、`config`、`cli`）。
- Step 按 L0–L8 分层放在 `steps/`；新模块需在 `steps/__init__.py` 中 import 以注册。
- 新数据集：在 `domain/schemas.py` 声明 schema + 主键、分区键，以及溯源列
  （`source`、`data_version`、`fetched_at`）。
- Adapter 保持薄（I/O 与源侧 quirks）；归一化放在 `steps/` / `domain/`。
- 单测默认离线；需要联网的测试须明确标记。
- 非平凡架构取舍写入 `docs/adr/`（复制 `0000-template.md`；ADR 正文保持英文）。

## 新增数据集清单

1. Schema + 主键 + 分区键
2. `@register_step`，填好 `depends_on` / `group` / `requires_workers`
3. 写时 schema 校验通过
4. 归一化单测 + 至少一个边界用例
5. 更新 [`docs/datasets/catalog.md`](docs/datasets/catalog.md) 与
   [`docs/datasets/sources.md`](docs/datasets/sources.md)
