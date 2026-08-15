## Context

当前知识链路：AGENTS.md（自动注入）→ `docs/`（权威 ADR / design / datasets）+ `agents/knowledges/`（速查，非权威）。OPSX 的 `/opsx:extract` 只能从**已归档的 OpenSpec change** 提取知识；不建 OpenSpec change 的讨论（确认型封闭问题、技术探索、方案对比）没有产出入口，结论随会话丢失。既有速查 `agents/knowledges/knowledge-flow.md` 定义了四层分流，但缺「捕获 → 暂存 → 提升」的可执行生命周期与命令入口。

约束：
- AGENTS.md 每会话自动注入，必须保持小且稳定（信噪比 / token 成本）。
- 检索应靠 grep 反向索引命中，而非 AGENTS.md 背条目。
- `docs/` 权威库不抄代码；速查不复制权威内容，只指路 `file:line`。
- 所有改动是 OPSX 命令 + 文档契约，不触碰 `src/` 运行时代码。

## Goals / Non-Goals

**Goals:**
- 讨论结论有一等公民的暂存区，写入成本远低于 ADR 归档。
- 从不依赖 OpenSpec 任务的讨论中也能沉淀知识。
- active notes 有界、archive 可清、AGENTS.md 恒稳（两级索引 + 生命周期回收）。
- 提供 `/opsx:note`（捕获）与 `/opsx:triage`（提升/整理）两个用户入口。

**Non-Goals:**
- 不去改 `/opsx:extract` 既有链路（它管已归档 change，与 note 链路互补）。
- 不做自动化 NLP 抽取（保留人工逐条确认，避免脏数据进库）。
- 不懂规则级校验、不产建造型 `agents/rules/` 内容（那是用户在 triage 时的可选动作）。
- 不尝试自动创建 OpenSpec change 或强制任何讨论转 OpenSpec 流程。

## Decisions

### D1：`docs/notes/` 作为暂存区（而非 `agents/knowledges/`）
notes 是**原始稿**（可草、含证据、含过期内容），`agents/knowledges/` 是**速查**（精炼、指路权威源）。原始稿混入速查目录会让速查层“可能是草稿”，破坏其定位。
- 选定：`docs/notes/`（active）+ `docs/notes/archive/`（冷存）。
- 备选：放 `agents/knowledges/notes/` — 否决，污染速查层的“已确认”语义。

### D2：两级索引——AGENTS.md 只记类别级地图
- 新增**类别**（首个 notes 话题 / 首个速查条目）→ AGENTS.md +1 行。
- 新增**常规条目** → 只在 `agents/knowledges/INDEX.md`（或 `docs/notes/README.md`）各 +1 行。
- 检索路径：AGENTS.md 定位类别 → `grep live INDEX/notes 关键词` → 命中才开文件。
- 理由：AGENTS.md 是每会话必注入的上下文，膨胀即恒久烧 token；grep 是反向索引，未命中零开销。

### D3：三态生命周期——active / promoted / stale，防单向增长
- 捕获写入 active，同 topic 再次捕获 **append 合并**（写即合并），增长按话题数不按提问次数。
- active 超过容量阈值（如 20 条）→ `/opsx:note` 提示先轻量 triage（容量预警）。
- `/opsx:triage` 时逐条判定：结论稳定 → 提升为 ADR / modules / datasets 补丁；属反复查询 → 折成 `agents/knowledges/` 速查；失效 → stale 删除；同一 topic 的 N 条 → 折叠为 1 条。
- promoted 条目：原始稿**转 `docs/notes/archive/`**，顶栏写「promoted → 权威源路径」；active 的 INDEX 行同步移除。不删除原始稿（保留实测证据 / file:line / 原始措辞），但 archive 不在热 grep 路径，不产漂移，triage 定期清理。
- 理由：active 是暂存队列不是归档库；archive 是冷存储，漂移风险归零由「权威源单一 + archive 不作为事实源」保证。

### D4：捕获经用户逐条确认（不自动全收）
`/opsx:note` 的三种调用：
- `/opsx:note <topic>` — 结合“当前对话”提炼 1-N 条候选，用户确认后写 active + INDEX。
- `/opsx:note <topic> <素材>` — 直接用素材成条。
- `/opsx:note --review` — 扫描整场会话，列候选清单逐条选择。
理由：extract 守则「never write without confirmation」延续；脏数据一旦进库比不进更糟。

### D5：命令形态对齐现有 OPSX（`agents/commands/opsx/*.md`）
- `/opsx:note.md`、`/opsx:triage.md`，frontmatter 风格与 `/opsx:extract.md` 一致。
- note 捕获模板（notes 文件正文）：`# <date>-<topic>.md` + `## 结论（1-2 行）` + `## 证据/出处（file:line / 实测数据）` + `## 状态: promising | promoted | stale`。
- 备选：做成 skill（SKILL.md）— 否决，OPSX 命令已有成熟入口且技能目录职责是技术能力而非流程。

## Risks / Trade-offs

- **triage 不执行 → active 无界增长** → 容量预警（≤20 条）在捕获路径内嵌兜底，把 triage 从「每周才做」变成「必要时顺手做」。
- **archive 只进不出 → 冷存储膨胀** → triage 定期清理旧 archive（稳定 N 周后删除或压缩），且 archive 不挂 AGENTS.md/INDEX，磁盘便宜、上下文不涨。
- **两级索引会造成「索引在哪」困惑** → `agents/knowledges/README.md` 显式列出两个索引位置；新类别才动 AGENTS.md 的规则写死在 knowledge-flow.md。
- **手工确认拖慢捕获** → 模板强制 1-2 行结论 + 预填证据，确认只花一次批复；`--review` 批量模式覆盖会话尾一次性沉淀。
- **notes 与权威 doc 漂移** → promoted 的 note 移出 active，权威内容只留在 ADR/速查单一事实源；archive 注释为「凭据非事实源」。