## 1. 目录与存储

- [x] 1.1 创建 `docs/notes/` 与 `docs/notes/archive/` 目录，写入 note 模板与 README（状态标记说明、`docs/notes/archive/` 为冷存储非事实源约定）
- [x] 1.2 创建 `agents/knowledges/INDEX.md`，说明条目级索引用途与登记格式
- [x] 1.3 在 `docs/notes/` 落地首条样例 note（如 daily-bars 讨论的结论 + 证据 + promising 状态），验证模板与路径

## 2. /opsx:note 捕获命令

- [x] 2.1 编写 `agents/commands/opsx/note.md`（frontmatter 与 `/opsx:extract.md` 一致），定义三种调用模式（当前对话提炼 / 素材直入 / `--review` 批量）
- [x] 2.2 定义 note 捕获模板与写即合并规则：命中同 topic 现有文件 → append 而非新建
- [x] 2.3 定义捕获确认守则：任何写入前必须经用户逐条确认；拒绝则零写入
- [x] 2.4 定义 active 容量预警：条目数达阈值（默认 20）时提示先执行轻量 triage
- [x] 2.5 定义 INDEX 登记动作并编排在写入步骤中：常规条目不动 AGENTS.md

## 3. /opsx:triage 整理命令

- [x] 3.1 编写 `agents/commands/opsx/triage.md`，列出全部 active notes 状态与结论供逐条决策
- [x] 3.2 定义提升动作：写 ADR / modules / datasets 补丁，原始 note 转入 archive 且顶栏加「promoted → 权威源」指针
- [x] 3.3 定义折叠动作：同 topic 多 note 合并为单一权威产物，INDEX 行折叠，archive 保留单条原始稿
- [x] 3.4 定义失效动作：标记 stale 并从 active INDEX 移除
- [x] 3.5 定义 archive 清理规则：超过保留窗口（如 N 周）的 archived note 删除或压缩，且不进 AGENTS.md / 热 grep 路径

## 4. 文档与约定更新

- [x] 4.1 更新 `agents/knowledges/knowledge-flow.md`：加入「捕获 → 暂存 → 提升」三段式生命周期、两级索引规则、触达铁律更新
- [x] 4.2 更新 `agents/knowledges/README.md`：显式列出两个索引位置（AGENTS.md 类别级、INDEX.md 条目级）
- [x] 4.3 修正 `AGENTS.md`：「新增文档 → 在本文件加一行索引」改为「新增类别才改 AGENTS.md，条目进 INDEX.md」；在「知识在哪里」补 `docs/notes/`（含 shed 分流说明）
- [x] 4.4 在 `AGENTS.md`「当前状态」登记「进行中的 change：add-discussion-knowledge-notes」及其目的简述

## 5. 验证

- [x] 5.1 走查 note 捕获全流程：会话中捕获一条 → 确认 → 落 active + INDEX；同 topic 再捕获 → append 合并
- [x] 5.2 走查 triage 全流程：提升一条（转 archive + 指针）、折叠同 topic 多条、标记一条 stale、验证 active 计数回落
- [x] 5.3 走查两级索引：新类别才动 AGENTS.md；常规条目 AGENTS.md 零变更；grep 按关键词命中 notes/INDEX
- [x] 5.4 校验仓库内规范：新建/修改文档均按现有中文书写约定与 frontmatter 格式，`/opsx:extract` 既有命令未受影响