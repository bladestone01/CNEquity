# agents/knowledges/INDEX — 条目级知识索引

**用途**：登记每个知识条目 / note（粒度：一条 = 一个 topic / 一份速查），供 grep 反向索引命中（关键词 → 行 → 文件）。

**与 AGENTS.md 的分工**：AGENTS.md 只记**类别级**地图（每会话自动注入，保持小且稳）；本条索引记**条目级**，常规新增条目只改这里，不动 AGENTS.md。

## 登记格式

一个条目一行，用 `|` 分隔，首次登记按捕获日期倒序插入表格：

```markdown
| 主题（grep 关键词习惯用词） | 文件 | 状态 | 捕获日期 | 一句话结论/指路 |
```

- **主题**：能让 grep 命中的关键词（如 `daily_bars`）。
- **文件**：相对仓库根的路径，`file:line` 可省略（note 正文里有）。
- **状态**：`promising` / `promoted` / `stale`（见 `docs/notes/README.md` 状态标记）。
- **捕获日期**：YYYYMMDD。
- **一句话结论/指路**：结论或权威源 `file:line`（promoted 直接写权威源）。

## 维护动作对照

| 事件 | 动作 |
|---|---|
| 新增 note / 速查条目 | 加一行（`promising`），AGENTS.md **零变更** |
| 首个**类别**出现 | 另在 AGENTS.md「知识在哪里」+1 行 |
| triage 提升 | 状态改 `promoted` + 结论改写为权威源指针 |
| triage 标记 stale | A 行删除；如需留证先转 `docs/notes/archive/` |
| triage 折叠同 topic | N 行合并为 1 行 |

域名速查（`agents/knowledges/*.md`）同样按此登记；`knowledge-flow.md` / `README.md` 本身是约定文档，不在此登记。

## 条目

| 主题 | 文件 | 状态 | 捕获日期 | 一句话结论/指路 |
|---|---|---|---|---|
| daily_bars | `agents/knowledges/daily-bars.md` | promoted | 20260815 | `daily_bars` 存未复权日 K（股票+ETF/LOF），仅 v2 行保证 volume 为股；复权走 `daily_bars_adj`；原始稿 `docs/notes/archive/20260815-daily-bars.md` |
| data-pipeline-flow | `agents/knowledges/data-pipeline-flow.md` | promoted | 20260816 | 数据湖六层流转：raw→staging→curated→derived→duckdb，meta管水位/质量，backups存手术残留 |