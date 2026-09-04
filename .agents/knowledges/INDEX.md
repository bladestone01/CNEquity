| 主题 | 文件 | 状态 | 日期 | 一句话结论 |
|---|---|---|---|---|
| cne-retry-run-anchor | docs/notes/20260822-cne-retry-run-anchor.md | promising | 20260822 | cne retry 的日期锚定 step 必须沿用 run 记录的 trade_date，否则会漂移到"今天"并可能补错日期 |
| daily-bars-incremental-window | docs/notes/20260822-daily-bars-incremental-window.md | promising | 20260822 | daily_bars 抓取窗口由 watermark 驱动（无水位回看 5 天）；按缺失符号增量只存在于同一 run 的 cne retry |
| daily-bars-symbol-granularity | docs/notes/20260822-daily-bars-symbol-granularity.md | promising | 20260822 | 失败归因到符号×日期缺口；完整性保障是 manifest failed 批→compact gate，而非抛异常 |
| instruments-master | docs/notes/20260822-instruments-master.md | promising | 20260822 | instruments 是证券主数据地基；list_date 为 null 会让"未上市/停牌前"豁免全部失效 |
| step-status-channel-failures | docs/notes/20260822-step-status-channel-failures.md | promising | 20260822 | 预期业务失败走 step 状态通道（status="failed"+结构化载荷），异常只留给真正的 bug |
| daily-bars-batch-failover | docs/notes/20260904-daily-bars-batch-failover.md | promising | 20260904 | worker_pool 进度中的 (100 symbols FAILED) 仅为单个批次异常隔离，系统自动触发备源快照与收尾 gap-fill/停牌豁免，不代表任务整体失败 |
