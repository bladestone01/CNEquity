# 数据湖流转速查

数据从外部源到查询端的六层流转路径。

## 流转链路

```
外部数据源
    ↓
raw/          ← 原始 HTTP 响应留存（可选）
    ↓
staging/      ← 按 run_id 隔离的采集落地，不保证 PK 唯一
    ↓  compact（PK dedupe + 原子写）
curated/      ← 下游只读的 canonical 数据，每个 PK 恰好一行
    ↓  derive steps
derived/      ← 可重算的派生数据（复权因子等）
    ↓  views.py 建视图
duckdb/       ← 终端查询入口
```

## 辅助目录

| 目录 | 职责 |
|------|------|
| `meta/` | 贯穿全程：manifest.db（runs/batches）、state/（水位）、quality/（审计）、locks/（文件锁） |
| `backups/` | 独立于 curated，存放手术残留（*.bak*）和元数据 tarball |

## 读写权限

| 路径 | 写入方 | 读取方 |
|------|--------|--------|
| staging | steps（采集） | compact |
| curated | compact | load() / DuckDB / 外部 Polars |
| derived | derive steps | load() |
| meta/* | orchestrator, quality, derive | CLI, audit, load |

## 权威源

- `docs/architecture/lake-layout.md` — 目录结构与读写权限
- `docs/architecture/data-flow.md` — 日更流程与 Compact 规则
