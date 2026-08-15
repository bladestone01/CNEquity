# docs/notes — 讨论结论暂存区

未走 OpenSpec 流程的讨论 / 技术探索 / 方案对比中产出的**原始稿** note。

## 是什么

| | 说明 |
|---|---|
| 定位 | 低成本暂存区：写入成本远低于 ADR 归档，供结论在后续会话中可复查 |
| 权威性 | 🟢 **非权威** — 原始稿（可草、可含过期内容），不是事实源 |
| 生命周期 | `promising`（新捕获）→ `promoted`（提升为 ADR/速查/权威 doc）/ `stale`（失效删除） |

## 目录结构

```
docs/notes/
  _template.md      # note 捕获模板（复制命名，勿直接改）
  <date>-<topic>.md # active notes：<date> 用 YYYYMMDD
  archive/          # 冷存储：promoted 条目的原始稿，**非事实源**
```

- active 与 archive 均为 `docs/notes/` 下的**暂存区**，权威内容只在 ADR / `docs/modules` / `agents/knowledges/`。
- **本文件与 `_template.md` 不登记入 INDEX**（非 note 条目）。

## 状态标记

每个 note 顶栏的 `## 状态:` 三态：

| 状态 | 含义 | 位置 |
|---|---|---|
| `promising` | 新捕获，尚未评审 | `docs/notes/` |
| `promoted` | 结论已提升为权威源，原始稿转冷存 | `docs/notes/archive/`（顶栏带 `promoted → 权威源路径` 指针） |
| `stale` | 已失效 / 被推翻 | 随 triage 移出 active（不删除，避免丢失证据则进 archive；否则删除） |

## 用法

- 捕获：`/opsx:note <topic>`（会话提炼）/ `/opsx:note <topic> <素材>`（素材直入）/ `/opsx:note --review`（批量）
- 整理：`/opsx:triage`（提升 / 折叠 / 失效 / 清理 archive）
- 检索：AGENTS.md「知识在哪里」定位类别 → grep 关键词命中 INDEX / notes → 命中才开文件

<!-- 约定：编辑本文件保留此布局；新增说明请维护到对应 docs/ 权威文档，这里只记暂存区自身约定。 -->