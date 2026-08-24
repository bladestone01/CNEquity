| 主题 | 文件 | 状态 | 日期 | 一句话结论 |
|---|---|---|---|---|
| daily_bars 符号级归因与严格缺口 | docs/notes/20260822-daily-bars-symbol-granularity.md | promising | 20260822 | 部分成功立即落盘、缺口按符号记账；完整性靠 compact gate 而非抛异常 |
| cne retry 重放 run 锚点 | docs/notes/20260822-cne-retry-run-anchor.md | promising | 20260822 | retry 日期锚定 step 沿用 run 记录的 trade_date，不漂移到今天 |
| daily_bars 增量窗口语义 | docs/notes/20260822-daily-bars-incremental-window.md | promising | 20260822 | 窗口=watermark+1..trade_date；无水位=5天回看；跨 run 全量重抓、同 run retry 才按缺口精拉 |
| 业务失败走状态通道 | docs/notes/20260822-step-status-channel-failures.md | promising | 20260822 | 预期失败用 status=failed + 结构化载荷，异常只留给真 bug |
| instruments 证券主数据排查参考 | docs/notes/20260822-instruments-master.md | promising | 20260822 | 证券主数据字段/多源填充/compact 合并；list_date=null 会导致新股豁免失效持续失败 |