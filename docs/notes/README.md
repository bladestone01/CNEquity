# docs/notes —— 会话结论暂存区

`/opsx:note` 捕获讨论/探索结论到此暂存，`/opsx:triage` 定期整理。

## 约定

- 命名：`<YYYYMMDD>-<topic>.md`，模板见 `docs/notes/_template.md`（`## 结论` / `## 证据/出处` / `## 状态: promising`）。
- **同 topic 一律 append 合并**，增长按话题数而不是提问次数。
- 写入前必须经用户逐条确认，拒绝则零写入。
- active 容量阈值：**默认 20 条**。达到后建议先 `/opsx:triage` 整理再继续捕获。

## 状态流转

- `promising`（active） → `/opsx:triage`：**promote**（升为 ADR/权威库） | **fold**（折叠为速查） | **stale**（失效删除）。
- promoted/stale 后移入 `docs/notes/archive/`（冷存储），不参与检索。
- INDEX 统一维护在 `.agents/knowledges/INDEX.md`。