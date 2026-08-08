# 数据集目录（逐源限制与更新频率）

各数据集的主源、更新频率与已知限制。

### 图例

- **波次（Wave）：** 日更批处理中的 step 名
- **按需（On-demand）：** 首次查询时由 `OnDemandService` 拉取

---

### MVP-P0

#### instruments

| 项 | 值 |
|------|-------|
| 波次 | `instruments`（Wave 0） |
| 主源 | tdx_protocol（内置 security_list） |
| 备源 | baostock（仅 `--backfill`，补退市标的） |
| 频率 | 每日 |
| 主键 | symbol |
| 股票池 | SH/SZ/BJ 前缀白名单 60/68/00/30/92 |
| 已知限制 | 快照中消失时推断 `delist_date`；东财补充 `list_date` |

#### trading_calendar

| 项 | 值 |
|------|-------|
| 波次 | `trading_calendar`（Wave 0） |
| 主源 | tdx_protocol |
| 备源 | 交易所 CSV |
| 频率 | 年度刷新 + 每日检查 |
| 主键 | trade_date |

#### trading_status

| 项 | 值 |
|------|-------|
| 波次 | `trading_status`（Wave 0） |
| 主源 | tdx_protocol |
| 备源 | eastmoney |
| 频率 | 每日 |
| 主键 | (symbol, trade_date) |

#### daily_bars

| 项 | 值 |
|------|-------|
| 波次 | `daily_bars`（Wave 1，依赖 corporate_actions） |
| 主源 | tdx_protocol（未复权，SH/SZ） |
| 备源 / 路由 | tip 缺口：eastmoney **clist**（分钟级）；多日窗口：eastmoney **kline**；BJ：sina |
| 频率 | 每日增量；init 时全量回填 |
| 主键 | (symbol, trade_date) |
| 重拉 | 当日 `corporate_actions` 的除权日对应标的 |
| 已知限制 | TDX 限速；建议 workers ≤ 8；clist 只有当日快照，须用 run 的 `trade_date` 打戳（ADR-0005 routing） |

#### index_bars

| 项 | 值 |
|------|-------|
| 波次 | `index_bars`（Wave 2） |
| 主源 | tdx_protocol |
| 备源 | eastmoney |
| 频率 | 每日 |
| 主键 | (symbol, trade_date, frequency) |

#### trade_ticks

| 项 | 值 |
|------|-------|
| 组 | `ticks`（不在任何默认调度上；`asl run daily --group ticks`） |
| 主源 | tdx_protocol（分笔命令 `0x0fb5`） |
| 备源 | **无，且这是有意的**（见下） |
| 频率 | 按需 / 手动 |
| 主键 | (symbol, trade_date, tick_seq) |

**为什么不设备源。** 备源的价值在于主源失败时还能拿到同一份数据，而分笔没有这样的替代品：

| 候选 | 历史深度 | 判断 |
|------|---------|------|
| TDX 历史分笔 | **回溯至 2024-01-02** | 唯一有历史深度的免费源 → 主源 |
| 腾讯（`stock_zh_a_tick_tx_js`） | 仅最近一个交易日 | 补不了历史 |
| 东财（`stock_intraday_em`） | 仅最近一个交易日 | 同上 |
| 新浪（`cn_bill.php`） | 近期，且**只给 ≥400 手大单** | 残缺 |
| 交易所 Level-2 | 完整逐笔 | **需付费授权，明确非目标** |

写一个只能补一天的备源，只会制造「有 fallback」的错觉——真正需要 fallback 的场景（回填历史）它一天都补不了。
所以 `failover` 不为 `trade_ticks` 登记备源：**单源即契约**，TDX 不可达时这个数据集就是拉不到。

#### commodity_bars

| 项 | 值 |
|------|-------|
| 组 | `macro_risk`（日更） |
| 主源 | eastmoney（国内主连）+ sina（外盘窄集：COMEX 金 `GC0.CMX`） |
| 回填 | `asl backfill commodity_bars`（默认自 2020-01-01；可用 `--start`/`--end`） |
| 主键 | (symbol, trade_date) |
| 已知限制 | 主连非真实交割月；夜盘归结算日；水位按 SSE 日历近似；海外无 egress 时国内主连可能空但新浪外盘仍可写；伦敦金等未收录 |

#### corporate_actions

| 项 | 值 |
|------|-------|
| 波次 | `corporate_actions`（Wave 1，先于 daily_bars） |
| 主源 | tdx_protocol 除权 |
| 备源 | eastmoney datacenter |
| 频率 | 每日 |
| 主键 | (symbol, ex_date, action_type) |
| 输出 | manifest 元数据 `symbols_to_rebackfill` |

#### adj_factors（derived）

| 项 | 值 |
|------|-------|
| Step | `derive_adj_factors`（finalize 波次） |
| 主源 | sina（qfq/hfq 因子序列） |
| 输入 | daily_bars 交易日 + 外部因子 API |
| 频率 | compact 之后每日 |
| 主键 | (symbol, trade_date, adjust_type) |
| 说明 | 外部累计因子对齐 daily_bars；`adj_close = close * factor` |
| **已知缺口** | **新浪因子序列基本不覆盖北交所**。实测本湖 6128 只股票中 260 只完全没有因子（252 只 BJ、5 只 SH、3 只 SZ）。`daily_bars` 走 TDX，覆盖北交所；两边因此不对齐 |
| **查询侧后果** | `load(adjust="hfq")` 默认 `strict_adj=False`，缺因子的行按 `factor=1.0` 返回，即**未复权价出现在复权结果里**，只由 `adj_is_exact=False` 标记。实测一年窗口 + `universe="all_a"`：10,480 行（0.77%）如此，其中 10,461 行 `close>0` 是真实价格，10,460 行是北交所 |
| **怎么办** | 只做沪深：`universe="all_a"` 之后再按 `adj_is_exact` 过滤；要严格失败而不是静默降级：`load(..., strict_adj=True)` |

---

### v1.0-full（第二批）

#### fund_flow

| 项 | 值 |
|------|-------|
| 分组 | capital@17:00 |
| 主源 | eastmoney |
| 主键 | (symbol, trade_date) |

#### northbound_holdings

| 项 | 值 |
|------|-------|
| 分组 | capital@17:00 |
| 主源 | eastmoney（`RPT_MUTUAL_HOLDSTOCKNORTH_STA`） |
| 主键 | 见 [schema.md](schema.md) |
| 已知限制 | 2024-08 起按季度披露，历史只能向前累积（EM 对历史 `TRADE_DATE` 返回 0 行） |

#### northbound_flows

| 项 | 值 |
|------|-------|
| 分组 | capital@17:00 |
| 主源 | eastmoney 沪深港通资金历史（`RPT_MUTUAL_DEAL_HISTORY`，`MUTUAL_TYPE` 001 沪股通 / 003 深股通） |
| 主键 | 见 [schema.md](schema.md) |
| 覆盖 | **2014-11-17 → 2024-08-16**（深股通自 2016-12-05）。回填：`asl backfill northbound_flows` |
| 已知限制 | 交易所自 **2024-08-19** 起停止披露每日北向净买入，此后所有行 `NET_DEAL_AMT` 为 null。这些行**不落盘**（不补零），因此水位永久停在 2024-08-16，`asl status` 会一直显示 STALE——这是源的事实，不是流水线故障 |
| 单位 | 报表金额列按 **百万元**，落盘换算为元。同一行的 `HOLD_MARKET_CAP` 却是元——该报表混用单位，改字段时要重新标定 |
| 一次一请求 | 该报表拒绝 `TRADE_DATE` 范围谓词（`InputMismatchException`），所以取全量后在本地切窗；两条通道全史约 5k 行 |

#### margin_trading

| 项 | 值 |
|------|-------|
| 分组 | capital@17:00 |
| 主源 | eastmoney |
| 主键 | (symbol, trade_date) |

#### valuation_metrics

| 项 | 值 |
|------|-------|
| 日更源 | eastmoney（clist 实时快照，覆盖当日 trade_date） |
| 历史源 | baostock（`asl backfill valuation_metrics`；按标的每日 PE/PB/PS 回填至 2016） |
| 主键 | (symbol, trade_date) |
| 已知限制 | baostock 历史含 pe_ttm/pb/ps_ttm；`float_mv`←amount/turn，`total_mv`←Q4 totalShare×close；日更 EM 快照覆盖最新交易日 |

#### announcement_index

| 项 | 值 |
|------|-------|
| 主源 | cninfo |
| 主键 | announcement_id |
| 说明 | 正文 on-demand（`announcement_body`）尚未实现；批量路径仅索引 |

---

### 按需数据集（On-demand）

不在日更波次中。缓存于 `meta/on_demand/`，可选写入 DuckDB 表。

| 数据集 | 来源 | 触发 |
|---------|--------|---------|
| stock_news | eastmoney | `asl query --dataset stock_news --symbol` |
| research_reports | eastmoney reportapi | 按标的 |
| announcement_body | cninfo | **未实现**（勿写入 `[on_demand].datasets`） |
| financial_reports | sina / gpcw | **未实现**（勿写入 `[on_demand].datasets`） |

---

### Meta 数据集

| 数据集 | 存储 |
|---------|---------|
| ingestion_runs | manifest.db |
| ingestion_batches | manifest.db |
| quality_findings | meta/quality/findings/ |
| source_diffs | meta/quality/source_diffs/ |
| data_catalog | 由 `asl catalog` 生成 |

---

### 源可用性矩阵

| 来源 | 协议 | MVP 用途 | 备源 | 降级策略 |
|--------|----------|-----------|--------|---------|
| tdx_protocol | TCP | bars、instruments、calendar | eastmoney clist（tip 路由）/ kline（多日） | tip 缺口进 curated（ADR-0005）；snapshot 供 diff |
| sina | HTTP | adj_factors（qfq/hfq） | — | 跳过该标的 + quality finding |
| eastmoney | HTTP | 公司行为备源、资金面 | — | 跳过 + quality finding |
| cninfo | HTTP | announcement_index | — | 仅按需 |
| baostock | TCP | 退市标的、历史 ST、估值回补 | — | 仅 `--backfill` |
| pboc | HTTP | 社会融资规模增量（`macro_indicators`） | — | 失败仅告警；取全量序列，下次运行补回 |
| nbs | HTTP | **仅审计**：PMI 发布稿，对照 `macro_indicators` | — | 缺省关闭；不可达时静默跳过 |
| exchange | HTTP | **仅审计**：上交所/深交所上市列表，对照 ST 标签 | — | 缺省关闭；不可达时静默跳过 |

> **AkShare 已不再被任何适配器调用**（[issue #3](https://github.com/rootSunc/ashare-lake/issues/3)）。
> 它此前的两个调用点分别指向本项目已经直连的端点：ST 集合走的是同一个东财
> push2 clist 板块与同一个 `fs` 过滤器，PMI / 货币供应量走的是同一批东财
> datacenter 报表。它提供的不是第二个口径，而是同一个口径外面的一层解析。
> 它也已从依赖里移除，`pip install ashare-lake` 不再装它。

调度与主备切换见 [运维 Runbook](../operations/runbook.md)。
