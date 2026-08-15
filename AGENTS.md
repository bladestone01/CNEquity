# ashare-lake — 共享 Agent 协作入口

本地可日更的 A 股研究湖（Python，uv 管理依赖），为人和 AI agent 保存可复查的历史。本文件为所有 vibe coding agent 的共享入口，说明项目的共享 agent 资源结构。

## 共享资源目录（单一事实来源）

所有工具（Claude Code / Codex / Gemini CLI）共享 `agents/` 目录下的资源，通过各工具点目录的 symlink 引用。**修改请改 `agents/` 下的文件**，不要改各点目录里的内容。

```
agents/
  commands/opsx/     # OPSX 命令（apply/archive/explore/extract/list/propose/ship）
  skills/            # 共享技能（SKILL.md 格式，含 graphify、git-commit、openspec-*、auto-skill-* 等）
  agents/            # 共享 subagent 定义
  knowledges/        # 项目知识（agent 高频速查）
  rules/             # AI 强制规则
  mcp/.mcp.json      # 共享 MCP 配置（根 .mcp.json 为 symlink）
```

- `.agents/`、根 `.mcp.json` 是指向 `agents/` 的 symlink，用于兼容各工具的读取约定。
- 各工具点目录（`.claude/`、`.codex/`、`.gemini/`）只保留**专有设置**（settings.json、settings.local.json 等），共享内容一律 symlink 到 `agents/`。

## OPSX 工作流

- `/opsx:explore` — 探索模式（需求澄清 + 方案对比）
- `/opsx:propose` — 生成完整提案（design/specs/tasks）
- `/opsx:apply` — 从 OpenSpec change 实现任务
- `/opsx:list` — 列出全部 OpenSpec changes 与状态
- `/opsx:extract` — 从已归档变更提取知识到知识库
- `/opsx:note` — 从会话/素材捕获讨论结论为 notes 暂存条目
- `/opsx:triage` — 整理 notes：提升/折叠/失效/清理 archive
- `/opsx:ship` — 发布：code review → commit → push → PR → 归档
- `/opsx:archive` — 归档已完成的变更

## 知识在哪里

| 类别 | 位置 | 说明 |
|---|---|---|
| 决策（业务决策 / 技术选型） | `docs/adr/` | ADR 格式，一决策一文件 |
| 设计 / 非显而易见的实现细节 | `docs/architecture/`、`docs/modules/` 等 | 按主题组织，只记 why / 约束 / 坑，不重复代码 |
| 用户与运维文档 | `docs/`（getting-started / operations / recipes / datasets / reference） | mkdocs 站点内容 |
| 规格与进行中的 change | `openspec/` | openspec 工件（首次使用时由 OPSX 创建） |
| agent 高频速查知识 | `agents/knowledges/` | 面向各 agent 的速查（非权威库）；分流原则见 `agents/knowledges/knowledge-flow.md` |
| 讨论结论暂存（notes） | `docs/notes/`（active）+ `docs/notes/archive/`（冷存） | 未走 OpenSpec 流程的讨论结论**暂存区**，非权威；捕获 `/opsx:note`，提升/清理 `/opsx:triage` |

## 当前状态

- 进行中的 change：`add-discussion-knowledge-notes`（讨论结论 notes 暂存链路：`docs/notes/` + 两级索引 + `/opsx:note`、`/opsx:triage` 命令）
- 最近决策：无
- agent 速查入账：`daily-bars.md`（存什么/何时触发）

## 协作规则

- 动代码前，先读相关 ADR 与 design 文档
- 做出新决策 → 新建 ADR，并在本文件"当前状态"加一行
- 讨论有结论 → 提升为 ADR / design，原始笔记标记"已提升"
- 实现细节以代码为准，文档不抄代码
- 新增**类别** → 在本文件「知识在哪里」加一行；常规文档/note 条目 → 只登记 `agents/knowledges/INDEX.md`（AGENTS.md 恒稳）
- 复杂变更/需求分析优先走 OpenSpec 流程（`/opsx:explore` + `/opsx:propose`）
- 需求确定后进入技术方案讨论 → 至少对比 **3 种可行方案**（各列优缺点/适用场景）再给推荐；见 `agents/rules/solution-options-comparison.md`
- 提交代码使用 `/opsx:ship` 或 `git-commit` 技能（conventional commits）
