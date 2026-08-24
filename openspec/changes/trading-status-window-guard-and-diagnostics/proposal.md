## Why

`trading_status` 在**非正常读取时段**（交易时段，如 09:14 早间运行 `cne run daily`）被触发时会误报为故障：主源东财 push2 被 IP 桶挡，备份 baostock 因当日会话尚未结算被新鲜度闸拒绝（`REFUSED reason: baostock has no data for D yet`），最终落下 `Step trading_status failed ERROR`。但日志只显示误导性的东财 clist 报错，真实原因（时间不对 / 备份拒绝原因）被吞掉，运维无法一眼归因。另外当日数据**已补齐成功**后的 rerun 是静默 0 行，缺少明确"已补齐"提示。

根因不是故障，而是三类体验/可观测性缺口：
1. 交易时段（16:00 前）抓当日 ST/停牌本无意义（快照未定稿、baostock 未结算），却按 ERROR 呈现；
2. rerun 已补齐时没有明确提示；
3. 失败/跳过时真实原因（stale / 未配置 / 非有效时段 / 阈值等）未浮出水面。

## What Changes

- **已覆盖短路**：`step_trading_status` 先判定当日 curated 是否已全量覆盖（observed ⊇ expected、非空）——已补齐则 `logger.info` + finding（`check="already_completed"`）明确提示"数据已补齐成功"，跳过一切网络采集直接返回。
- **时段守卫**：当前交易日 16:00（Asia/Shanghai）之前且未覆盖时，跳过采集并给出明确提示"此非正常数据读取时间段（应 16:00+ 后执行，待有效数据生成后重跑）"；步骤返回 `status="warning"` + finding（`check="before_cutoff"`）。历史日回补、非交易日、backfill 不受影响。
- **真实原因浮现**：备份协调器拒绝时，`_fetch` 不再 `raise primary_exc` 原样抛出，而是组合出一条可归因错误：`trading_status: primary(eastmoney) failed; backup declined: <reason>`（`from primary_exc`）；skip/已补齐同样给出带 `check` 词的日志与 finding。

## Capabilities

### New Capabilities

- `trading-status-run-guards`: trading_status 日更步骤的运行时守卫与可观测性——已覆盖短路提示、16:00 前时段守卫、失败/跳过的真实原因浮现（词汇表 `already_completed` / `before_cutoff` / `backup_declined`）。

### Modified Capabilities

<!-- 当前仓库无既有 openspec/specs，无需 delta spec。 -->

## Impact

- 修改：`src/cnequity/steps/reference.py`（三个守卫判定 + step 编排 + `_fetch` 异常组合）、`tests/unit/test_trading_status_failover.py`（新增守卫用例）。
- 可选微调：`src/cnequity/quality/failover.py` 各拒绝分支补 `logger.warning`（reason 字段已存在，无逻辑变更）。
- 不涉及：schema、主键、分区、写路径、failover 路由本身。
- 行为语义：`status="warning"` 仅出现在"时段外跳过的当日"；`already_completed` 为 info。
- 部署：datalake 为 editable 安装，改 `src` 即生效；无需配置变更。