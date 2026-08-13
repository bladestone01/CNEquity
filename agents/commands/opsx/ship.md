---
name: "OPSX: Ship"
description: "Ship completed work — commit, push, create PR, cleanup worktree, and archive OpenSpec change"
category: "OPSX"
tags: [workflow, delivery]
---

Ship completed work: commit worktree changes, push branch, create a Draft PR, cleanup worktree, and archive the OpenSpec change.

**Input**: Optionally specify a change name after `/opsx:ship` (e.g., `/opsx:ship add-auth`). If omitted, infer from conversation context or prompt for selection.

**Steps**

1. **Identify the change**

   If a name is provided, use it. Otherwise:
   - Infer from conversation context
   - Auto-select if only one active change exists
   - If ambiguous, run `openspec list --json` and use **AskUserQuestion tool** to let the user select

   Always announce: "Using change: <name>"

2. **Check worktree state**

   Determine the current working context:
   ```bash
   git rev-parse --is-inside-work-tree
   git branch --show-current
   git worktree list
   ```

   Identify:
   - Current branch (worktree branch name)
   - Whether we're in a worktree or main checkout
   - The base branch (where this worktree was created from)

3. **Code Review**

   Before shipping, run code review to catch issues:
   - Use **Skill tool** to invoke `review` skill for a code review of the current diff
   - Use **Skill tool** to invoke `qa` skill for quality check
   - Fix any issues found before proceeding
   - Key checkpoints (see project rules):
     - PageHelper DTO conversion: `.agents/rules/pagehelper-dto-conversion.md`
     - Controller OpenAPI annotations: `.agents/rules/controller-openapi-standard.md`
     - Unit test coverage: `.agents/rules/test-requirement.md`

4. **Check for uncommitted changes**

   ```bash
   git status --short
   ```

   - If changes exist: stage all and commit with a descriptive message derived from the change name and proposal
   - If no changes: skip commit step

   **Commit message format**: `<type>(<scope>): <summary>`

   Use the proposal.md content to derive the commit message type, scope, and summary.

5. **Push branch**

   ```bash
   git push origin <current-branch>
   ```

   If push fails (e.g., no remote), note the error and ask user how to proceed.

6. **Create Draft PR**

   Create a Draft Pull Request using GitHub CLI:

   ```bash
   gh pr create --draft \
     --title "<type>(<scope>): <summary>" \
     --body "## 变更说明

   <description from proposal>

   ## 变更类型

   - [ ] 新功能 (feat)
   - [ ] Bug 修复 (fix)
   - [ ] 重构 (refactor)
   - [ ] 性能优化 (perf)
   - [ ] 测试 (test)
   - [ ] 文档 (docs)
   - [ ] 构建/工具 (chore)

   ---

   🤖 Generated with [Claude Code](https://claude.com/claude-code)"
   ```

   - If `gh` is not installed or PR creation fails, report and ask user for alternative approach
   - Display the PR URL after creation

7. **Cleanup worktree and remote branch**

   **Remote branch** — automatically delete (no user confirmation needed):
   ```bash
   git push origin --delete <current-branch>
   ```
   If deletion fails (branch already deleted or never pushed), note it and continue.

   **Local worktree** — ask user whether to clean up:

   Use **AskUserQuestion tool**:
   - "Keep worktree for future work?" → skip cleanup
   - "Clean up worktree and delete local branch?" → execute:
     ```bash
     # From main checkout directory
     git worktree remove <worktree-path>
     git branch -d <worktree-branch>
     ```

8. **Archive OpenSpec change**

   ```bash
   mkdir -p openspec/changes/archive
   DATE=$(date +%Y-%m-%d)
   mv openspec/changes/<name> openspec/changes/archive/${DATE}-<name>
   ```

   If delta specs exist at `openspec/changes/<name>/specs/`, ask user whether to sync to main specs before archiving.

   If the change directory doesn't exist, skip this step with a note.

9. **Auto-extract knowledge**

   After archiving, automatically extract knowledge from the change into the project knowledge base:

   - Use **Skill tool** to invoke `openspec-extract` skill with `--auto` mode:
     ```
     openspec-extract <change-name> --auto
     ```
   - This will:
     - Read the change artifacts (proposal, design, tasks)
     - Analyze for business rules, design decisions, technical constraints, best practices, and integration specs
     - **Auto-classify**: technical constraints → `.agents/rules/`, other knowledge → `docs/` 对应分类（business / architecture / technical / integration）
     - **Deduplicate**: skip entries already in the knowledge base
     - Write entries without user confirmation
   - If the change was not archived (skipped in step 8), also skip this step
   - Display the extract summary as part of the ship summary

10. **Display summary**

    ```
    ## Ship Complete 🚢

    **Change:** <change-name>
    **Committed:** <commit-hash> <commit-message>
    **Branch:** <branch-name>
    **PR:** <pr-url>
    **Worktree:** ✓ Cleaned up (or kept)
    **Remote branch:** ✓ Deleted
    **Archived to:** openspec/changes/archive/YYYY-MM-DD-<name>/
    **知识提取:** N 条知识已写入知识库 (M 条重复已跳过)
    ```

**Guardrails**
- This is an independent command — it does NOT auto-delegate from `/opsx:apply`; the user invokes it manually
- Run code review before committing, fix any issues found
- Do NOT merge to main/master directly — always create a Draft PR instead
- Never force-push (`--force`) or force-merge
- If no git changes exist, skip commit and PR steps with a note
- If `gh` is not available, report and ask user how to proceed
- If worktree cleanup fails, report error and ask user
- If OpenSpec change directory doesn't exist, skip archive with a note
- Preserve commit history — do NOT squash or rebase without asking
