## Why

讨论、技术探索、系统分析中产生的大量有价值结论（领域口径、数据坑、方案对比、数据证据），当前要么沉淀进 `docs/`（已拍板结论，门槛高），要么走 `/opsx:extract`（只吃已归档的 OpenSpec change）。大量**未走 OpenSpec 流程**的讨论结论无处安放，随后续会话丢失——没有低成本暂存区，也没有从暂存到权威库的提升路径。

## What Changes

- 新增 `docs/notes/` 作为讨论结论的**暂存区**（raw notes），一话题一文件，带状态与日期。
- 新增 `docs/notes/archive/` 冷存已提升条目的原始稿（带指向权威源的指针）。
- 新增 `agents/knowledges/INDEX.md` 两级索引：条目级索引在此，AGENTS.md 只记类别级地图。
- 新增 `/opsx:note` OPSX 命令：会话中/结束时捕获讨论结论，确认后按模板写入 notes + INDEX。
- 新增 `/opsx:triage` OPSX 命令：周期性整理 notes——提升为 ADR/速查、合并同类、标记失效、清理。
- 更新 `agents/knowledges/knowledge-flow.md`：补充「捕获 → 暂存 → 提升」三段式生命周期与两道闸（写即合并、active 容量预警）。
- 顺带修正 AGENTS.md「新增文档 → 在本文件加一行索引」为「新增**类别**才改 AGENTS.md，条目进 INDEX.md」。

## Capabilities

### New Capabilities
- `note-capture`: 捕获讨论结论为 notes 条目（/opsx:note 命令、note 模板、写即合并、INDEX 登记）。
- `note-triage`: notes 周期性整理与提升（/opsx:triage 命令、主动三态判定、归档/清理动作）。
- `notes-store`: `docs/notes/` + `docs/notes/archive/` 目录结构、文件形态、状态标记与会话外读取约定（两级索引）。

### Modified Capabilities
<!-- 无既有 spec 调整 -->

## Impact

- **新增文件**：`docs/notes/**`、`agents/knowledges/INDEX.md`、`agents/commands/opsx/note.md`、`agents/commands/opsx/triage.md`。
- **修改文件**：`agents/knowledges/knowledge-flow.md`（补生命周期）、`AGENTS.md`（索引规则改写成两级）。
- **不变**：`docs/` 权威库形态、`/opsx:extract`（管已归档 change 的知识）与本次新增的 note 链路互补。
- **无运行时代码改动**：全部是 OPSX 命令 + 文档 + 目录约定，不触碰 `src/`、`pyproject.toml`。