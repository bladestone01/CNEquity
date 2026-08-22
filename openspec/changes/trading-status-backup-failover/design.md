## Context

`trading_status`（ST/停牌日快照）目前唯一数据源是 EastMoney，经 `tdx_protocol/client.py::fetch_trading_status` 门面分两条腿抓取：

1. ST 腿：push2 `clist/get`（`fs=m:0+f:4,m:1+f:4` 风险警示板），host 池 `push2 → push2delay → 40.push2`。
2. 停牌腿：datacenter `RPT_CUSTOM_SUSPEND_DATA_INTERFACE`。

2026-08-18 连续两次日更失败（21:15 / 21:32）。实测确认两个独立故障叠加：本机海外出口触发 push2 对源 IP 的突发节流（502/断连/无 data 三态，静默 20~90s 恢复，与客户端、pz 无关）；且停牌接口已被 EastMoney 改契约（`code=9501`：先要求 `DATETIME`，后要求 `MARKET`），**即便网络正常主路径也拿不到停牌**。

baostock 是当前唯一可用、独立、schema 兼容的替代源：`query_all_stock(day)` 单请求返回 SH/SZ 全市场 `(code, tradeStatus, code_name)`；ST 名称前缀与东财板 **203/203 零偏差**；停牌快照与逐股 k-data 自洽；四列输出与 `TRADING_STATUS_SCHEMA` 匹配（`backfill_source="baostock"` 本就是既有契约）。

## Goals / Non-Goals

**Goals:**
- 主源（EastMoney）失败时，`trading_status` 仍能产出当日数据，且**不伪造当日真实状态**（宁缺勿假）。
- SH/SZ 用 baostock 单请求兜底；BJ 降级但可计数、可告警。
- 降级全程可审计：动态 provenance、`source_snapshots`、步骤 `warning`、audit findings。
- 修复 EastMoney 停牌腿的 9501 契约漂移，恢复主路径停牌抓取。
- 保持 `TRADING_STATUS_SCHEMA` 与既有 `_fetch` 完整性校验不变。

**Non-Goals:**
- 本 change 不负责 `[sources.eastmoney].proxy` 配置与 `min_interval_seconds` 调优（运维项，海外出口根治手段）。
- 不补齐 BJ 的 ST 标签（两源均不覆盖，既有缺口，主源亦然）。
- 不做"透明等切换"的长期双跑比对（需先修好东财停牌腿后才能验证，属后续增量）。
- 不改 schema、不引入新依赖（baostock 已是 `[sources.baostock]` 既有源）。
- **不提供配置驱动的主源替换**：`[[failover.datasets]].primary` 仅元数据，运行时主腿固定先调 EastMoney（与 daily_bars/corporate_actions 现状一致）。
- **不把 ST 与停牌拆成独立数据集**：二者同属单一 `trading_status`（同一 schema/step/watermark），主备差异收敛在适配器字段级，配置只有 dataset 一个旋钮。

## Decisions

### D1. 协调器放 `quality/failover.py`，不动 TDX 门面

在 `quality/failover.py` 新增 `fetch_trading_status_backup(config, symbols, trade_date)`，由 `step_trading_status` 的 `_fetch` 闭包在主路径异常时调用；`tdx_protocol/client.py` 保持只管 EastMoney。

- **依据**：与 `daily_bars`/`corporate_actions` 的 failover 模式同构（`failover_spec(config, dataset)` 门控 + `write_backup_snapshot` 留档），改动面最小、可复用审计链路。
- **备选**：在 client.py 内联兜底——否决：client.py 是 tdx 门面，不知道 failover spec，职责不符。
- **约定**：`spec.primary`（配置里的 `primary="eastmoney"`）为文档标注，不参与路由；"先主后备"由 `_fetch` 闭包代码固定。backup 选用由 `spec.backup` 决定（读取 `backup="baostock"`）。

### D2. baostock 单请求 `query_all_stock(day)` 而非逐股 k-data

兜底适配器用 `query_all_stock(day)` 一次拿全市场；不沿用现有 `fetch_st_history` 的逐股 `query_history_k_data_plus` 扫。

- **依据**：逐股扫约 5400 请求，日更不可行且有免费 API 黑名单风险（`_session.py` 注释：batch 20/rest 120s 才安全）；单请求几乎零成本。
- **代价**：baostock 无 BJ（D4 处理）；`tradeStatus=0` 是"当日未交易"而非严格"停牌"，对当日已上市 universe 影响极小，适配器文档注明。

### D3. 新鲜度硬闸：宁缺勿假

备份前用参照股（如 600519.SH）k-data 探针确认 baostock 已含当日 `D`；若只有 D-1，**拒绝备份**并给出 "backup stale" 明确失败原因，绝不把昨日状态盖今日 `trade_date` 写库（`_validate_trade_date` 本就强制日期一致）。

- **依据**：老数据在 schema 上是合法的（不会报错），却是静默污染——已复牌标 suspended / 新停牌标 normal。
- ST 与停牌的过时容忍不同，但两者出自同一快照，按"整体新鲜"二值处理；为单维拆时间容忍只会引入复杂度。

### D4. SH/SZ → baostock，BJ → 东财腿尽力则默认 normal+计数

备份协调器按交易所切分：SH/SZ 全量走 baostock；BJ 先复跑东财停牌腿（datacenter 可能与 push2 独立存活），失败则全部 `normal` 并计入 `n_bj_defaulted`。

- **备选**：A（默认 normal+告警，推荐） vs B（BJ 缺即整体失败）。选 **A**：BJ 约 250 只、当日停牌 0~5 只，整表缺失比零星误标危害更大；降级被 warning+findings 显式暴露。
- BJ ST 两源均无 → 与主源现状一致（无新增恶化）。

### D5. 补行分类防洗错：昨日基线 + 阈值

缺失 SH/SZ 行按四类处理，核心规则：**"昨日 curated 为 suspended"的缺行绝不补 `normal`**（续停被洗为可交易属数据污染），记 fill-failure；其余按权限补 `normal` 并计数；累计填充数超阈值（默认 `max(50, 1% of universe)`）拒绝备份。

- **依据**：`_fetch` 的 observed==expected 是硬闸，缺行必须处理；"昨天 suspended→今天缺失"是唯一能从旧数据推知的高危场景，必须挡住。
- fill 计数兼任"baostock 过时/异常"的哨兵（缺行膨胀是 S2/S3 的间接信号）。

### D6. 动态 provenance + 显式降级

`step_trading_status` 不再硬编码 `source="eastmoney"`：降级帧 stamp `baostock`（BJ 默认行也标 baostock，属推断值而非真实东财数据）；`response.failover_used / n_filled / n_bj_defaulted` 汇入 `context_updates.audit_findings`；步骤返回 `status="warning"`；`write_backup_snapshot` 落 `meta/source_snapshots/trading_status`。

### D7. 修复东财停牌腿契约（独立 capability）

`eastmoney/trading_status.py::_fetch_suspended_symbols` 需按 2026-08 实测反解出的新 datacenter 契约改造：

- **filter 形态**：`(DATETIME='D')(MARKET="...")`——`DATETIME` 用**单引号**日期（虚拟批次字段，非输出列），`MARKET` 用**双引号**字符串；旧的 `(STOP_DATE<=...)(RESUME_DATE>=...)` 组合已不满足（报 `code=9501`，先要 DATETIME 再要 MARKET）。
- **MARKET 枚举（已实测确认）**：`沪市A股`、`深市A股`、`科创板`、`创业板`、`京市A股`；其余如 `主板`/`北交所`/`A股` 均为非法值。五个市场各查一次，按 `SECURITY_CODE` 去重。
- **列重命名（输出列已变）**：`STOP_DATE → SUSPEND_START_DATE`、`RESUME_DATE → SUSPEND_END_TIME`。`DATETIME` 不是输出列。
- **schema 边界（决策）**：按项目决定，`TRADING_STATUS_SCHEMA` 核心列不变；适配器**只请求/消费** `SECURITY_CODE,SUSPEND_START_DATE,SUSPEND_END_TIME`，`SUSPEND_EXPIRE/SUSPEND_REASON/PREDICT_RESUME_DATE/SECURITY_NAME_ABBR/TRADE_MARKET` 属上游元数据，**不入 schema、不请求**（如需根因/复牌预测分析时另立配套数据集的假设已暂缓，选项 1）。
- **覆盖语义**：按 "D 批次快照" + `SUSPEND_START_DATE<=D` 与（`SUSPEND_END_TIME` 空或 `>=D`）判定当日停牌。
- **空批次必须 fail-loud**：`fetch_datacenter` 对 `9201 返回数据为空` 走 break 返回 `[]`——新 adapter 必须把"五市场全空"判定为批次缺失并 raise（不能静默当"无停牌"），"部分市场空"记 warning 继续。
- 复用 `fetch_datacenter` 的 schema-rejection（`EastMoneyDatacenterError`）语义；`MARKET` 枚举见 Open Questions 更新。

## Risks / Trade-offs

- **停牌跨源一致性未证实**（东财腿当前损坏，无法双跑比对）→ 先修 D7，修复后跑"同日主源 vs baostock diff"脚本，偏差清零才声明透明切换。
- **baostock as-of 时点**：日更核心波次若在 16:00 触发，`query_all_stock(D)` 可能尚无当日数据 → D3 新鲜度闸拒绝备份（宁缺）；建议依赖备份的组排在 18:00 后。
- **补行把真停牌洗成 normal** → D5 昨日-suspended 规则硬性拦截 + fill 计数审计。
- **BJ 零散误标**（默认 normal 期间真停牌 BJ 被标可交易）→ 影响小而固定，warning+findings 暴露；universe 过滤侧维持现状。
- **push2 节流仍是主源常态**（海外出口）→ 备份只是兜底；根治靠 proxy/降速（Non-Goals）。
- **free-API 稳定性**：baostock 单日仅 1 请求，黑名单风险可忽略；但会话登录失败需复用 `_session.py` 的重试/重登。
- **`*st` 词表**：现役东财也只产 `st`；baostock 名称含前缀可按需升级拆 `*st`，不阻塞当前 schema。

## Migration Plan

1. 先合 D7（东财停牌修复）：改动局部、可独立回归。
2. 再合 D1-D6（baostock 兜底）：默认**不启用**——`[[failover.datasets]] trading_status` 不写进配置即零行为变化；datalake 与 example 配置里按需打开。
3. 上线后在 datalake 跑 `cne run daily` 观察：正常日主源路径不变；故障日观察 warning+finding+snapshot。
4. 回滚：删除 `[[failover.datasets]]` 条目即回到现状（纯配置门控）。

## Open Questions

- ~~**EastMoney `MARKET` 枚举**~~（已实测反解，见 D7）：合法值 = `沪市A股`/`深市A股`/`科创板`/`创业板`/`京市A股`（双引号字符串）；`DATETIME` 用单引号日期。残留疑问：五个 market 查询之间是否存在重复/覆盖缝隙（如 深市A股 已含创业板、科创板是否只走科创标签），需在实测数据上核验并固定去重顺序。另：`DATETIME` 批次的可用历史深度（已见 08-14~08-19 连续有货）需在修复后长期观察。
- **BJ 默认 normal 语义**（D4 方案 A）是否需要产品/风控确认；若下游对 BJ 停牌极其敏感，改方案 B。
- **空批次阈值**：把"五市场全空即 raise"做死，还是允许配置 `allow_empty_market_batch` 以容忍回购/无新增停牌日——需业务确认。
- **阈值与容量**：`max(50, 1% of universe)` 是否合适，是否做成 `[[failover.datasets]]` 可调项。
- **参照股选型**：用 600519.SH 是否足够代表性（几乎不停牌、必含在 k-data），还是用多参照股投票。