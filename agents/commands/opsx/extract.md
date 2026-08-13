---
name: "OPSX: Extract"
description: "从已归档的变更中提取知识到项目知识库"
category: "OPSX"
tags: [workflow, vibe-coding]
---

从已归档的 OpenSpec 变更中提取业务规则、设计方案、技术约束和集成规范，写入项目知识库。

**Input**: 可选参数为 change-name（如 `/opsx:extract fix-paginated-dto-total-loss`）。如果省略，将从已归档变更列表中让用户选择。

**Steps**

1. **Select the change to extract from**

   If a name is provided and it's an archived change (under `openspec/changes/archive/`), use it directly.

   If the name matches an active (non-archived) change, warn the user that the change hasn't been archived yet and confirm they want to proceed anyway.

   If no name provided, list available archived changes:
   ```bash
   ls openspec/changes/archive/
   ```
   Use the **AskUserQuestion tool** to let the user select from the list.

2. **Read the change artifacts**

   Read the following files from the change directory (the path is either `openspec/changes/archive/<date>-<name>/` for archived, or `openspec/changes/<name>/` for active):
   - `proposal.md` — What was changed and why
   - `design.md` — Architecture decisions and trade-offs
   - `tasks.md` — Implementation steps and any gotchas encountered
   - `specs/` directory (if exists) — Delta capability specs

   If the directory or core files don't exist, report the error and abort.

3. **Analyze for knowledge in 4 dimensions**

   Based on the content read, identify knowledge entries across these dimensions:

   | Dimension | Source | What to look for |
   |---|---|---|
   | **业务规则** (Business Rules) | proposal.md, specs | Data validation rules, status transitions, business constraints, calculation logic |
   | **系统设计方案** (Design Decisions) | design.md, proposal.md | Architecture choices with rationale, rejected alternatives, data model changes, API design patterns |
   | **技术约束/最佳实践** (Technical Constraints) | tasks.md, git diff | Pitfalls encountered, performance optimizations, framework-specific constraints, security requirements |
   | **集成规范** (Integration Specs) | design.md, proposal.md | External system integration methods, authentication, retry strategies, configuration items |

   For each dimension, extract 0-N knowledge entries. Each entry should be specific and actionable.

4. **Present entries for user confirmation**

   Show each extracted knowledge entry grouped by dimension. Use the **AskUserQuestion tool** for each entry with options:
   - "保留并写入知识库" (default/recommended)
   - "跳过此条目"
   - "修改内容后写入"

   For technical constraints, also ask:
   - "写入 .agents/rules/ 作为 AI 强制规则" (recommended)
   - "仅写入知识文件作为参考"

5. **Write approved entries to knowledge base**

   Based on the confirmed entries, write to the appropriate files:

   | Type | Target File | Strategy |
   |---|---|---|
   | 业务规则 | `docs/business/BUSINESS_RULES.md` | Append new section; create file if not exists |
   | 设计方案 | `docs/architecture/DESIGN_DECISIONS.md` | Append new section; create file if not exists |
   | 技术约束 | `.agents/rules/<rule-name>.md` | Create new rule file or append to existing one |
   | 最佳实践 | `docs/technical/BEST_PRACTICES.md` | Append new section; create file if not exists |
   | 集成规范 | `docs/integration/INTEGRATION_SPECS.md` | Append new section; create file if not exists |

6. **Display summary**

**Guardrails**
- Always let the user confirm each knowledge entry before writing
- Never write to knowledge base without user confirmation
- For technical constraints, always offer to create a `.agents/rules/` file
- If the knowledge file already exists, read it first and append to it (don't overwrite)
- Use the existing format conventions of the target files
- Skip entries where content is too vague or generic to be actionable
