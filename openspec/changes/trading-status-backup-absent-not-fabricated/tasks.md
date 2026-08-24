## 1. 备源语义改造（capability: trading-status-backup-absent-not-fabricated）

- [x] 1.1 `quality/failover.py::_fill_missing`：返回值扩展为 `(fill_rows, fill_failures, n_filled, n_missing_unseen)`；"前日无记录 → 造 normal"分支改为**不发行 + 计入 `n_missing_unseen`**；"前日 normal→顺延"与"前日非交易→fill_failure"保持
- [x] 1.2 `quality/failover.py::fetch_trading_status_backup`：meta 增加 `n_missing_unseen`（经 `_fill_missing` 透传）；`n_filled` 只统计顺延行，阈值判定逻辑不变
- [x] 1.3 `steps/reference.py::step_trading_status`：读 backup meta，`n_missing_unseen > 0` 时写 audit finding（`check="trading_status_backup_unseen_missing"`，message 带计数）；`=0` 不上finding
- [x] 1.4 确认不触碰：`_NON_TRADABLE_STATUSES`、阈值公式、`_baostock_has_day` 新鲜度、空快照拒绝、compact gate、schema、CLI

## 2. 测试

- [x] 2.1 `test_quality_failover`：无前记录缺失 → 输出无该符号行 + meta `n_missing_unseen==N`（`n_filled` 不含它）
- [x] 2.2 前日 normal 缺失 → 顺延 normal、`n_filled` 计入、`n_missing_unseen` 不计（回归）
- [x] 2.3 前日非交易缺失 → fill_failure 整体拒绝（回归）
- [x] 2.4 `step_trading_status`：N>0 产生 finding、N=0 无 finding
- [x] 2.5 阈值回归：仅顺延行计入 `n_filled`，超阈值仍整体拒绝

## 3. 验证与交付

- [x] 3.1 `ruff check src tests` 全绿 + `pytest tests/unit -q` 全量通过
- [x] 3.2 CHANGELOG 记录（trading_status 备源不再编造无记录 normal；新增 `n_missing_unseen` 观测计数）
- [ ] 3.3 冒烟（可选，需 datalake）：EastMoney 挂时 backup 接受场景确认 `n_missing_unseen` 出现在 step 输出/audit