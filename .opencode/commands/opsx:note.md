---
description: "从会话/素材捕获讨论结论为 notes 暂存条目（/opsx:note）"
---

捕获讨论、技术探索、方案对比中产出的结论到 `docs/notes/` 暂存区，无需走 OpenSpec 流程。核心铁律：**任何写入前必须经用户逐条确认，拒绝则零写入**。

**Input**：三种调用模式，二选一必带 `<topic>`。

## 步骤

1. **确定调用模式**

   | 模式 | 用法 | 行为 |
   |---|---|---|
   | 当前对话提炼 | `/opsx:note <topic>` | 回顾当前会话，提炼 1-N 条候选（每条含 1-2 行结论 + 证据位置），逐条确认后写入 |
   | 素材直入 | `/opsx:note <topic> <素材>` | 直接用素材成条，不扫会话，仍须逐条确认 |
   | 批量（会话尾） | `/opsx:note --review` | 扫描整场会话列候选清单，逐条 选择/编辑/跳过 确认 |

2. **写即合并（命中先读，append 不新建）**

   落盘前先检查是否命中同 topic 现有文件：

   ```bash
   ls docs/notes/
   grep -l "<topic 关键词>" .agents/knowledges/INDEX.md   # INDEX 命中 →
   ```

   - **命中**同 topic 的 active note → **append** 到现有文件（新增一节），不新建文件；INDEX 行更新结论/证据。
   - **未命中** → 按 `docs/notes/_template.md` 新建 `docs/notes/<YYYYMMDD>-<topic>.md`。
   - 标题 `# <YYYYMMDD>-<topic>`，正文按模板：`## 结论（1-2 行）` / `## 证据/出处（file:line | 实测数据）` / `## 状态: promising`。

3. **捕获确认守则（before writing）**

   - 逐条向用户展示候选（结论 + 证据），用 Question 工具确认「保留 / 跳过 / 修改」。
   - **用户拒绝全部 → 零写入**（不建文件、不动 INDEX）。
   - 未确认的条目绝不落盘（含 INDEX 登记）。

4. **active 容量预警**

   ```bash
   ls docs/notes/*.md | grep -v -E 'README|_template' | wc -l
   ```

   条数 ≥ 阈值（**默认 20**，`docs/notes/README.md` 可调）→ 写入前提示：
   > active notes 已达 N（阈值 20），建议先 `/opsx:triage` 做轻量整理再继续捕获。

5. **INDEX 登记（随写入编排）**

   - 每写入一条，同步在 `.agents/knowledges/INDEX.md` 加一行（主题 | 文件 | `promising` | YYYYMMDD | 一句话结论）。
   - 常规条目只编辑 `.agents/knowledges/INDEX.md`，不触碰 AGENTS.md（本仓库若暂无 AGENTS.md 则跳过）。

6. **完成后报告**

   列出本次捕获的条目与 INDEX 变更；若被提示容量预警，附 triage 建议。

**Guardrails**

- 永远先确认后写入；拒绝则零写入（含 INDEX）。
- 同 topic 一律 append 合并，不建重复文件——增长按**话题数**不按提问次数。
- 证据必须给出 `file:line` 或实测数据，结论控制在 1-2 行，保留 `## 状态:` 段。
- 常规条目只编辑 `.agents/knowledges/INDEX.md`，不碰 AGENTS.md。
- 素材直入模式不扫会话，避免把无关谈话掺进条目。