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
| 主源 | tdx_protocol（mootdx security_list） |
| 备源 | akshare |
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
| 主源 | tdx_protocol（未复权） |
| 备源 | eastmoney |
| 频率 | 每日增量；init 时全量回填 |
| 主键 | (symbol, trade_date) |
| 重拉 | 当日 `corporate_actions` 的除权日对应标的 |
| 已知限制 | TDX 限速；建议 workers ≤ 8 |

#### index_bars

| 项 | 值 |
|------|-------|
| 波次 | `index_bars`（Wave 2） |
| 主源 | tdx_protocol |
| 备源 | eastmoney |
| 频率 | 每日 |
| 主键 | (symbol, trade_date, frequency) |

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

---

### v1.0-full（第二批）

#### fund_flow

| 项 | 值 |
|------|-------|
| 分组 | core@16:30 |
| 主源 | eastmoney |
| 主键 | (symbol, trade_date) |

#### northbound_holdings / northbound_flows

| 项 | 值 |
|------|-------|
| 分组 | capital@16:30 |
| 主源 | eastmoney |
| 主键 | 见 [schema.md](schema.md) |

#### margin_trading

| 项 | 值 |
|------|-------|
| 分组 | signals@17:00 |
| 主源 | eastmoney / akshare |
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
| 说明 | 全文走 on-demand `announcement_body` |

---

### 按需数据集（On-demand）

不在日更波次中。缓存于 `meta/on_demand/`，可选写入 DuckDB 表。

| 数据集 | 来源 | 触发 |
|---------|--------|---------|
| announcement_body | cninfo | `asl query --dataset announcement_body --symbol` |
| stock_news | eastmoney / akshare | 按标的 |
| research_reports | eastmoney reportapi | 按标的 |
| financial_reports | sina / gpcw | 按标的 |

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
| tdx_protocol | TCP | bars、instruments、calendar | eastmoney | 仅 audit 告警 |
| sina | HTTP | adj_factors（qfq/hfq） | — | 跳过该标的 + quality finding |
| eastmoney | HTTP | 公司行为备源、资金面 | akshare | 跳过 + quality finding |
| cninfo | HTTP | announcement_index | — | 仅按需 |
| akshare | HTTP | 可选 | — | 默认关闭 |

调度与主备切换见 [运维 Runbook](../operations/runbook.md)。
