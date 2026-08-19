# docs/notes/archive —— 已沉淀/失效笔记冷存储

promoted（已升权威源）或 stale（失效）的笔记移入本目录，作为"凭据"保留，**不再参与检索**（不进 INDEX、不参与热 grep）。

- 保留窗口：**默认 8 周**。超过窗口的 archived note → 删除或压缩（幂等：appendix 精炼或 zip 归档）。
- archived note 顶部保留指针注释：

  ```markdown
  > promoted → 权威源：`docs/adr/0006-xxx.md`（YYYYMMDD 提升，凭据非事实源）
  ```

- 归档动作由 `/opsx-triage` 执行；本 README 同步记录保留窗口，按需调整。