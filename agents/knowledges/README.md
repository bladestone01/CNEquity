# agents/knowledges — 项目知识（面向 agent）

面向各 vibe coding agent 的项目知识文件（技术指南、约定、排查手册）。
项目权威知识库仍在 `docs/`（ADR / design / notes），本目录放 agent 高频查阅的速查类知识。
新增条目前先读 `knowledge-flow.md`（知识分流原则）。

## 两级索引位置（先查索引，再开文件）

| 粒度 | 位置 | 说明 |
|---|---|---|
| **类别级** | `AGENTS.md`「知识在哪里」表 | 每会话自动注入，只记类别地图；仅新增**类别**才 +1 行 |
| **条目级** | `agents/knowledges/INDEX.md` | 每条速查 / note 登记一行；常规条目只更新这里，AGENTS.md 零变更 |

检索：类别从 AGENTS.md 定位 → grep `INDEX.md` / `docs/notes/` 关键词 → 命中才读文件。

## 索引

| 文件 | 主题 |
|---|---|
| `knowledge-flow.md` | 知识分流原则：讨论/分析产物该写 docs 还是这里 |
| `INDEX.md` | 条目级索引：登记每个速查 / notes 条目（two-level 的条目那级） |
| `daily-bars.md` | `daily_bars` 存什么、何时触发、易踩的坑 |
