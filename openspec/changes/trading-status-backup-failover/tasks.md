## 1. 修复东财停牌接口契约（capability: eastmoney-suspension-fetch）

- [x] 1.1 按反解契约改造 `adapters/eastmoney/trading_status.py::_fetch_suspended_symbols`：输出列仅取 `SECURITY_CODE,SUSPEND_START_DATE,SUSPEND_END_TIME`（不再请求 `STOP_DATE/RESUME_DATE`；`SUSPEND_EXPIRE/SUSPEND_REASON/PREDICT_RESUME_DATE/SECURITY_NAME_ABBR/TRADE_MARKET` 元数据字段不请求、不入 schema）
- [x] 1.2 filter 改造：`(DATETIME='<D>')(MARKET="<market>")`（DATETIME 单引号日期、MARKET 双引号字符串）；按 `沪市A股/深市A股/科创板/创业板/京市A股` 五市场循环查询，按 `SECURITY_CODE` 去重
- [x] 1.3 `_suspension_covers` 改用 `SUSPEND_START_DATE`/`SUSPEND_END_TIME`（兼容 `YYYY-MM-DD HH:MM:SS` 与 null），覆盖语义维持 `START<=D` 且（`END` 空或 `>=D`）
- [x] 1.4 空批次策略：五市场全空 → `EastMoneyDatacenterError`（不许静默当"无停牌"）；部分市场空 → warning 继续
- [x] 1.5 实测 2026-08-18 停牌集合与 baostock 交叉核对（600984/002084/002445/002906/300176），确认两源一致
- [x] 1.6 单测 `test_eastmoney_trading_status_adapter.py`：9501 抛错、9201 空批次 raise、列映射、窗口覆盖、五市场去重

## 2. baostock 日更适配器（capability: trading-status-failover）

- [x] 2.1 新建 `adapters/baostock/trading_status.py`：`fetch_trading_status_baostock(symbols, trade_date, *, bs=None, sleep=…, config=None)`，复用 `_session.py` 的 `_login`/超时/重登基建
- [x] 2.2 实现单请求 `query_all_stock(day=trade_date)` → 四列映射（`tradeStatus` 0/1 → `is_trading`/`status`；名称前缀 ST → `st`；非法词汇 → None 触发重试），过滤非 sh/sz 与非请求集符号
- [x] 2.3 导入 `exchange/st_lists.py::is_st_name` 判 ST 前缀，避免重复实现
- [x] 2.4 单测 `test_baostock_trading_status_adapter.py`：fake `rs`（`fields`/`next()`/`get_row_data()`）验证映射、ST 前缀、未知值、符号过滤、不误标 BJ

## 3. failover 协调器与快照

- [x] 3.1 `quality/failover.py` 新增 `fetch_trading_status_backup(config, symbols, trade_date)`：`failover_spec(config,"trading_status")` 门控 + `config.sources.get("baostock", True)` + `config.failover_enabled`
- [x] 3.2 实现 SH/SZ→baostock、BJ→先东财停牌腿（`_fetch_suspended_symbols`）再默认 normal+计数 的拆分逻辑
- [x] 3.3 实现新鲜度闸：参照股（600519.SH）k-data 探针确认 baostock 已含当日 D；过时返回"backup stale"原因
- [x] 3.4 实现补行分类：读上一日 curated `trading_status` 基线；昨日 suspended 的缺行记 fill-failure 不补 normal；其余补 normal+计数；超阈值（默认 max(50, 1% universe)）拒绝备份
- [x] 3.5 新增 `snapshot_trading_status_backup(...)`：走 `write_backup_snapshot` 落 `meta/source_snapshots/trading_status`（source=baostock）
- [x] 3.6 返回结构化结果 `(df, meta)`：`failover_used / n_filled / n_bj_defaulted / freshness`，供步骤侧生成 findings

## 4. step 接线与降级可见性

- [x] 4.1 `steps/reference.py::step_trading_status`：`_fetch` 闭包在主路径异常（或空帧）时调用协调器；保留 observed==expected 与 `_validate_trade_date` 硬闸
- [x] 4.2 动态 provenance：按实际来源 stamp `source`（baostock / eastmoney），替换硬编码 `source="eastmoney"`
- [x] 4.3 降级时 `result["status"]="warning"` + `context_updates.audit_findings`（n_filled / n_bj_defaulted / freshness / stale 原因）
- [x] 4.4 兼容 `_backfill_trading_status_st`（backfill 路径不受影响）

## 5. 配置与文档

- [x] 5.1 `configs/cnequity.example.toml` 增加 `[[failover.datasets]] name="trading_status" primary="eastmoney" backup="baostock"`（注释说明默认关闭/用途；条目为 dataset 粒度：同时覆盖 ST 与停牌）
- [x] 5.2 更新 `docs/datasets/sources.md` trading_status 条目：备份链、BJ 降级、已知边界
- [x] 5.3 修正 `steps/reference.py:246` "EastMoney 是唯一每日 ST 源"的过时注释
- [x] 5.4 CHANGELOG.md 记录本次变更
- [x] 5.5 对齐 `domain/datasets.py` 中 `trading_status` 的 `primary_source/backup_source` 与新建 failover 条目（`primary="eastmoney", backup="baostock"`），或以注释明示两者语义差异（dataset 描述符 vs 运行时 failover 配置）

## 6. 测试与验证

- [x] 6.1 单测 `test_trading_status_failover.py`：主路径成功→备份不触发；主路径抛异常→备份走通；spec 缺失/baostock 关→不劫持；baostock 全挂→仍 raise
- [x] 6.2 单测 fill 分类与新鲜度闸：昨日 suspended 缺行不补 normal；探针过时→拒备份；超阈值→拒备份
- [x] 6.3 单测 provenance/warning：降级帧 stamp baostock + findings 内容
- [x] 6.4 `uv run ruff check src tests && uv run pytest tests/unit -q` 全量通过
- [x] 6.5 手动冒烟（datalake）：`[sources.eastmoney] enabled=false` 跑 `cne run daily --group core`，核对 staging/curated 有 trading_status、来源=baostock、状态=warning、snapshot 落盘；随后恢复主源重跑一次确认正常路径不变

## 7. 交付

- [x] 7.1 双跑比对脚本（东财修复后）：同一交易日主源 vs baostock 输出 diff，输出 ST/停牌两侧一致性报告
- [x] 7.2 更新 `openspec/changes` 归档（sync specs）并在 CHANGELOG 标注降级语义
