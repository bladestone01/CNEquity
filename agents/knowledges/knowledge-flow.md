# 知识分流原则（knowledge-flow）

讨论 / 分析 / 迭代中产生的知识如何入库。核心判据：**这条知识是要被当作事实引用的，还是要被快速检索引用的。**

## 四层分流

| 内容形态 | 去往 | 权威性 | 写入策略 |
|---|---|---|---|
| **设计决策** — 已拍板的结论（为什么选某方案、某技术选型、ADR） | `docs/adr/` | 🔴 权威 | 一决策一文件，填 ADR 模板；完成后在 AGENTS.md「当前状态」加一行 |
| **设计细节** — schema 契约、单位口径、模块边界、数据坑 | `docs/modules/` `docs/datasets/` `docs/architecture/` | 🔴 权威 | 按主题组织，只记 why / 约束 / 坑，不抄代码 |
| **高频速查** — 反复被问的 Q&A、关键词定位表 | `agents/knowledges/` | 🟡 速查 | **只写答案 + 指路权威源 file:line**，绝不复制权威内容，否则两处漂移 |
| **强制约束** — 后续 agent 必须遵守的规则 | `agents/rules/` | 🔴 强制 | 写清「做什么 / 不做什么 / 为何」 |
| **讨论过程原始稿（notes）** | `docs/notes/` + `docs/notes/archive/` | 🟢 暂存（非权威） | 经 `/opsx:note` 捕获，`/opsx:triage` 提升/折叠/失效；结论稳定后进权威库，原始稿转冷存 |

## 三段式生命周期（捕获 → 暂存 → 提升）

不依赖 OpenSpec 流程的讨论结论，走「暂存区 → 权威库」的可执行链路：

1. **捕获** `/opsx:note`：会话/素材提炼候选，**逐条用户确认**后按模板写入 `docs/notes/<YYYYMMDD>-<topic>.md`（状态 `promising`），并登记 `agents/knowledges/INDEX.md`。同 topic 再次捕获 → **append 合并**，不新建文件。
2. **暂存**：active notes 是有界队列（默认阈值 20，超限先轻量 triage）。非权威、可草、含证据；不参与 agent 权威引用。
3. **提升** `/opsx:triage`：逐条判定 → 提升为 ADR / modules / datasets 补丁（结论稳定）、折成 `agents/knowledges/` 速查（反复查询）、或标记 stale（失效）。promoted 原始稿**转 `docs/notes/archive/`**，顶栏加 `promoted → 权威源` 指针；**权威内容只留在权威库单一事实源，archive 是冷存储凭据非事实源**，定期按保留窗口清理。

## 两级索引（AGENTS.md 恒稳）

知识触达靠**两级索引**，AGENTS.md 保持小且稳定：

| 粒度 | 位置 | 何时更新 |
|---|---|---|
| **类别级** | AGENTS.md「知识在哪里」表 | 仅新增**类别**（如首个 notes 话题 / 首个速查域）才 +1 行 |
| **条目级** | `agents/knowledges/INDEX.md` | 每个常规条目 / note 登记一行 |

- 检索路径：AGENTS.md 定位类别 → **grep** INDEX / notes 关键词 → 命中才开文件。
- **常规条目只编辑 INDEX.md，AGENTS.md 零变更**；它每会话自动注入，膨胀即恒久烧 token。

## 判据速查（三问）

1. **它是一锤定音的决策吗？** 是 → `docs/adr/`。不是，但定义了事实 → 看 2。
2. **它是「分析时会被反复查询」的口径/含义吗？** 是 → `agents/knowledges/` 速查条目（指向权威源）。它的权威定义是否已有正式载体 → 有则只指路，无则先补权威 doc 再指路。
3. **它是不做就会出错的约束吗？** 是 → 除知识文件外另写 `agents/rules/` 强制规则。

## 触达铁律

**入库 ≠ 生效。** 一段知识只有在后续 agent 能自动触达时才被使用。触达路径自上而下：

1. AGENTS.md「知识在哪里」（**类别级**）＋ `agents/knowledges/INDEX.md`（**条目级**）← 新类别才动 AGENTS.md，常规条目只登记 INDEX，未登记 = 孤岛
2. 代码注释、README、错误信息里的 `file:line` 指路
3. 关键词命中 glob/grep 兜底（先 grep 再开文件，未命中零开销）

不满足任意一条触达路径的知识，等于留在会话里——下次不会被读到。

## 典型产出 → 去往

| 持续分析里常见产出 | 去往 |
|---|---|
| 「某 dataset 存什么、何时触发、单位是什么」 | `agents/knowledges/<dataset>.md` |
| 「这个 bug 为什么这样修 / 为什么选这条路由」 | `docs/adr/` 或模块 design |
| 「x 和 y 对不上账，差 100 倍」 | 权威补丁进 `docs/datasets/schema.md` + 速查条目 |
| 「改代码前必须先读什么」 | `agents/rules/` |