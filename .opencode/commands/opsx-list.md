---
description: List all OpenSpec changes and specs in the system
---

List all OpenSpec changes and optionally specs in the system.

**Input**: Optionally pass `--specs` to list specs instead of changes. If omitted, lists changes by default.

**Steps**

1. **Run the list command**
   ```bash
   openspec list --json
   ```

2. **Display changes as a table**

   Parse the JSON output and display a formatted table with:
   - Change name
   - Status (with emoji indicator)
   - Progress (completed/total tasks)
   - Last modified (relative time)

   Status indicators:
   - `complete` → ✅ Complete
   - `in-progress` → 🔧 In Progress
   - `no-tasks` → 📝 No Tasks
   - `blocked` → 🚫 Blocked

3. **Show summary**

   Display counts by status and suggest next actions.

**Output**

```
## OpenSpec Changes

| Change | Status | Progress | Last Modified |
|--------|--------|----------|---------------|
| ocr-integration | 🔧 In Progress | 20/22 | 2 days ago |
| file-access-refactor | 🔧 In Progress | 16/18 | 1 day ago |
| ocr-passport-integration-test | ✅ Complete | 18/18 | 5 hours ago |
| custom-meta-object-handler | 📝 No Tasks | 0/0 | 10 minutes ago |

### Summary
- ✅ Complete: 3
- 🔧 In Progress: 4
- 📝 No Tasks: 1

### Next Actions
- `/opsx-apply <change>` to continue implementation
- `/opsx-propose <name>` to create a new change
- `/opsx-list --specs` to view specifications
```

**When `--specs` is passed**

Run `openspec list --specs --json` and display specs in a similar table format.

**Guardrails**
- Always use `--json` for reliable parsing
- Sort by last modified (most recent first) by default
- If no changes exist, suggest creating one with `/opsx-propose`
