---
description: "整理 notes 暂存区：提升/折叠/失效/清理 archive"
---

周期性整理 `docs/notes/` 的 active 暂存队列，防止单向增长。逐条与用户确认后执行动作；与 capture 的写即合并没有冲突——**此处是按条决策去向**。

**Input**：无参数，直接 `/opsx:triage`。

## 步骤

1. **列出全部 active notes**

   ```bash
   ls docs/notes/*.md | grep -v -E 'README|_template'
   ```

   对每条显示：标题（日期-topic）、`## 结论`、`## 证据/出处`、`## 状态`，供逐条决策。附 active 计数。

2. **逐条判定 → 三态动作**

   | 判定 | 动作 | 结果 |
   |---|---|---|
   | 结论稳定、可作权威 | **提升 promote** | ADR / `docs/modules` / `docs/datasets` 补丁 + note 转 archive + 指针 |
   | 反复被查询的口径/答案 | **折叠/指路 fold** | `.agents/knowledges/` 速查条目（指权威源）+ note 标记 promoted + 转 archive |
   | 被推翻 / 过期 | **失效 stale** | 标记 stale，INDEX 行删除（要留证据先转 archive） |

3. **提升动作（promote）**

   - 按目标库模板写权威产物：决策 → `docs/adr/<NNNN-*.md>`；口径/模块细节 → `docs/modules/`；数据集 → `docs/datasets/schema.md` 补丁。
   - 原始 note **move** 到 `docs/notes/archive/<原文件名>`，并**在文件顶部加**：
     ```markdown
     > promoted → 权威源：`docs/adr/0006-xxx.md`（YYYYMMDD 提升，凭据非事实源）
     ```
   - active 的 INDEX 行**同步移除**（design D3：promoted 移出 active 索引；ADR 另在 `docs/adr/` 登记，速查条目在 `.agents/knowledges/INDEX.md` 登记为 `promoted`）。

4. **折叠动作（fold）**

   - 同 topic 的多条 note 合并为**单一权威产物**（1 个 ADR 或 1 条速查），N 个 INDEX 行压缩为 1 行（速查条目在 `.agents/knowledges/INDEX.md` 登记为 `promoted`，结论 = 速查文件指针）。
   - 原始稿全部转 archive，**每个 topic 仅保留一条**原始稿（保序拼接，顶栏加合并说明 + 指针）。
   - archive 中其余被折叠条目删除或作为附录压缩，不各自保留索引行。

5. **失效动作（stale）**

   - note 顶栏 `## 状态:` 改 `stale`（或直接删除）；从 active INDEX 移除该行。
   - 若含未沉淀的证据，先 move 到 archive（实为「保留凭据」语义），否则直接删除。目的：active 计数回落。

6. **archive 清理（防冷存膨胀）**

   - 超过保留窗口（**默认 8 周**，`docs/notes/archive/README.md` 记录并用；按需调整）的 archived note → 删除或压缩（保幂等：appendix 精炼或 `zip` 归档）。
   - archive **永不进 AGENTS.md / INDEX / 热 grep 路径**——检索只从 active + 权威源走。

7. **收尾报告**

   - 本次动作汇总：提升 X 条 / 折叠 N→1 / stale M 条 / 清理 C 条 archived。
   - active 最新计数与容量水位（阈值默认 20）：`ls docs/notes/*.md | grep -v -E 'README|_template' | wc -l`。

**Guardrails**

- 每条先展示再确认，绝不批量默认；用户不同意就不执行该条。
- 权威内容只写入权威库（ADR / modules / datasets / 速查），archive note 永远保留「凭据非事实源」注释。
- 同 topic 折叠后只保留单条 archive 原始稿，避免证据重复膨胀。
- archive 是冷存储：不登记、不参与 grep、定期按保留窗口清理。