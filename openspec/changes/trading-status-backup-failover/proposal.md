## Why

`trading_status` 每日步骤持续失败（2026-08-18 两次复现，21:15/21:32），导致当天 ST/停牌数据整表缺失。根因有两个独立问题叠加：

1. **网络层**：EastMoney push2 对本机（海外出口）IP 做"突发桶"节流，表现为 502 nginx / `Server disconnected` / 200-但无 `data` 三态交替（实测：连发 36 请求后 curl/httpx/curl_cffi 全部 502，静默 20~90s 自动恢复；`40.push2` 还伴随 TCP 直连失败）。
2. **接口层**：EastMoney 停牌腿 `RPT_CUSTOM_SUSPEND_DATA_INTERFACE` 已被 schema 变更破坏——生产 filter 直接 `code=9501`（先要求包含 `DATETIME`，补上后又要求 `MARKET`，取值语义不明）。**即便 push2 网络正常，主路径也拿不到停牌。**

baostock 是当前唯一可用且自洽的独立源：`query_all_stock(day)` 单请求覆盖 SH/SZ 全市场，ST 标签与东财风险警示板实测 **203/203 零偏差**，停牌数据内部一致（快照 `tradeStatus=0` 与逐股 `tradestatus` 全对上），输出 4 列与 `TRADING_STATUS_SCHEMA` 兼容（且本就是 `backfill_source="baostock"` 的既有契约）。

## What Changes

- **新增 baostock 日更适配器** `fetch_trading_status_baostock`：单请求 `query_all_stock(day)`，映射 `symbol/trade_date/is_trading/status`（`tradeStatus=0`→`suspended`，名称前缀含 ST→`st`），输出与 `TRADING_STATUS_SCHEMA` 兼容。
- **新增 failover 协调器**：`quality/failover.py` 新增 `fetch_trading_status_backup`，由 `[[failover.datasets]] name="trading_status" primary="eastmoney" backup="baostock"` + `config.failover_enabled` 门控；备份被采用时写 `source_snapshots`（source=baostock）。
- **拆分 universe**：SH/SZ 走 baostock；BJ 先复用东财 datacenter 停牌腿（若存活）再退回默认 `normal`，并记录 `n_bj_defaulted` 到 audit findings。
- **新鲜度硬闸**：备份前用参照股探针（600519.SH 等）验证 baostock 已含当日 D 数据；过时则**拒绝备份**（宁缺勿假），绝不把昨日状态盖当日 `trade_date` 写入。
- **补行分类防洗错**：缺行分四类处理（新上市/异常缺失/已退市残留/BJ），"昨日 suspended 的缺行"坚决不补 `normal`（续停被洗为正常属数据污染）；超过阈值（默认 1% universe 或 50 只）整体拒绝备份。
- **动态 provenance + 显式降级**：`step_trading_status` 按实际数据来源 stamp `source`（否则默认 eastmoney）；降级时步骤 `status="warning"` + `context_updates.audit_findings`，让下游与 `cne audit` 能识别降级日。
- **修复东财停牌接口契约**（独立 capability）：适配 `RPT_CUSTOM_SUSPEND_DATA_INTERFACE` 新增的 `DATETIME`/`MARKET` 要求；摸清取值枚举或换报表。
- `_fetch` 的完整性校验（observed==expected）在备份模式下作为硬闸保留；四类适配差异（code→symbol、缺行补齐、tradeStatus=0 语义边界、`*st` 词表）在适配器内闭环。
- **配置粒度声明**：`trading_status` 是**单一数据集**（ST 与停牌共用 `TRADING_STATUS_SCHEMA`、同一 step/watermark/分区），`[[failover.datasets]]` 按 dataset 粒度生效——一条 `name="trading_status"` 同时覆盖 ST 与停牌，**不按字段细分**主备配置；`primary` 字段仅作元数据标识，运行时主腿固定先调 EastMoney（与 daily_bars/corporate_actions 现状一致），主源替换不在本 change 提供配置驱动路由。

## Capabilities

### New Capabilities

- `trading-status-failover`: trading_status 日常抓取的主备切换——东财失败时按 universe 拆分走 baostock 兜底，含新鲜度闸、补行分类、动态 provenance、audit findings 与 source_snapshots 留档。
- `eastmoney-suspension-fetch`: 东财停牌腿适配器按新 datacenter 契约（`DATETIME`/`MARKET` filter）修复，恢复主路径停牌抓取能力。

### Modified Capabilities

<!-- 当前仓库无既有 openspec/specs，无需 delta spec。 -->

## Impact

- 新文件：`src/cnequity/adapters/baostock/trading_status.py`、`tests/unit/test_baostock_trading_status_adapter.py`、`tests/unit/test_trading_status_failover.py`
- 修改：`src/cnequity/quality/failover.py`（协调器 + snapshot）、`src/cnequity/steps/reference.py`（step 接线/来源/降级）、`src/cnequity/adapters/eastmoney/trading_status.py`（9501 契约修复）、`configs/cnequity.example.toml`（`[[failover.datasets]]`）
- 无 schema 变更（baostock 4 列输出与 `TRADING_STATUS_SCHEMA` 兼容）；`st_coverage`/audit 机制沿用。
- 部署注意：datalake 装的是 site-packages wheel，改动需重新 `pip install -e .` 才生效。
- 已知降级边界（非本 change 范围）：BJ 的 ST 两源均不覆盖（既有缺口）；`[sources.eastmoney].proxy` 与 `min_interval_seconds` 调优属运维项。