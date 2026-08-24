---
description: Use for any question about a codebase, its architecture, file relationships, or project content — especially when graphify-out/ exists, where the question should be treated as a graphify query first
---

Run the graphify skill: load `SKILL.md` from `.opencode/skills/graphify/SKILL.md` and follow it exactly (version pin in `.graphify_version`, references in `references/`). If `graphify-out/graph.json` already exists and the request is a natural-language codebase question, use the skill's fast path: `graphify query "<question>"`.

Only produce `graphify-out/` artifacts; never modify the scanned source tree.