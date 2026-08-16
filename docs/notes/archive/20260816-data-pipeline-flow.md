# 20260816-data-pipeline-flow

> promoted → 权威源：`agents/knowledges/data-pipeline-flow.md`（20260816 提升，凭据非事实源）

## 结论（1-2 行）

- 数据湖六层流转：`raw/` → `staging/`(按run_id隔离) → `curated/`(PK唯一，compact去重+原子写) → `derived/`(可重算派生) → `duckdb/`(视图查询)；`meta/` 贯穿全程管水位/质量/锁，`backups/` 独立存手术残留。

## 证据/出处

- `docs/architecture/lake-layout.md:10-18` — 顶层目录结构与各层职责
- `docs/architecture/lake-layout.md:161-169` — 读写权限约定表（stages→compact→curated→load）
- `docs/architecture/data-flow.md:43-68` — 日更流程：采集写staging → compact → derive → audit
- `docs/architecture/data-flow.md:73-86` — Compact 规则：PK dedupe + 原子写 + failed batch 跳过

## 状态: promising
