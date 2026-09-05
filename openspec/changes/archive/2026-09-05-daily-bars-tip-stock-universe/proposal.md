## Why

`step_daily_bars` 日更 tip 用 `load_symbols(config)` 全量 universe 扫批，而该 universe（实测 7420 只）包含 **~600 只场内基金 / ETF / REIT**（`asset_type="etf"`，如 562110.SH、159585.SZ、158010.SZ、526060.SH）。这些标的在增量窗口内大多没有 TDX 日线，触发 `worker_pool.py:84 _require_daily_bar_symbol_coverage` 的**严格逐股覆盖门槛**——批内一只无行情即整批 RuntimeError，manifest 记录 `TDX returned no rows for 1 requested symbol(s): X`，整批 100 只全被标 FAILED。

实测（2026-08-14..08-19 多次 run）：batch-0/-100/-400/-500/-800/-900/-1200 依次因 `301655.SZ/562110.SH/159585.SZ/159756.SZ/561690.SH/158010.SZ/526060.SH` 失败，且完全确定性复现。`cne sources --only tdx_protocol` 只测服务器连通性，测不出该问题。

`step_daily_bars_history`（bars.py:1105）**已经**把 universe 过滤到 `asset_type=="stock"`——日更 tip 缺了同样的过滤，两侧不一致。

**批级爆炸半径**：`_run_batch` 对一枚"无行"符号整体抛错，`_require_daily_bar_symbol_coverage` 在 staging 前失败 → 整批 100 只作废并全部进入 failover 重拉。过滤后仍残留的股票场景（新股上市窗口内无日线、整窗口停牌、退市边界）会继续以"一只毒倒一百只"的方式发作——这需要把失败**归因到符号级**，并在"本就没有可抓数据"（未上市/已退市/整窗口停牌）时**豁免**而不是判失败。

## What Changes

- `step_daily_bars` 的 tip 全市场扫批 universe 过滤为 `asset_type == "stock"`（排除场内基金/ETF/REIT/其它非股票），与 `step_daily_bars_history` 的口径对齐。
- **失败归因到符号级**：批内某只股票 TDX 无行不再让整批作废——正常股票照常收 TDX 结果落盘，只有真正缺行的符号进入 failover 重拉。
- **"合法空"豁免**：未上市（`list_date > 窗口结束`）、已退市（`delist_date < 窗口开始`）、整窗口停牌 的符号从"必有行"集合剔除、记 finding，不判失败、不发 failover 空跑。
- **"意外缺口"仍严格**：窗口内本应有行的股票 TDX 返回空 → 判失败并入既有 failover（tip 用 EastMoney clist，多日用 kline），语义不变。
- **处理粒度可配置**：新增 `[orchestrator] daily_bars_granularity` 开关（`symbol` | `batch`，默认 `symbol`），动态决定 daily_bars 的抓取/落盘/归因/重试策略；`batch` 模式完整保留现有"整批 all-or-nothing"的严格语义作为精确回退。
- **策略以配置文件为唯一入口**：粒度**只读配置值**，`cne run daily` 与 `cne backfill` **不提供** `--granularity` 命令行逃生口（与既有"策略入配置、一次性范围入 CLI"的分寸一致；`--granularity` 属处理策略而非抓取范围）。rerun/重试按 run 启动时记录的实际粒度执行——`symbol` 模式精确重拉失败符号（符号×日期），`batch` 模式保持整批重拉；临时实验须改 config（跑后改回），靠 run metadata 留痕可审计。
- **符号模式的两条硬规则**：①失败范围以"符号×日期"粒度持久化到 manifest（failed-scope），保证跨界重试只重拉真缺口；②重试写入 attempt 级的新批次文件（`part-{batch_id}-{attempt}.parquet` 语义），杜绝复用确定性 batch_id 覆盖已落盘的部分成功数据；写盘前 anti-join 掉已由主源覆盖的键（守住 ADR-0005 的"备份不能覆盖主源行"）。

## Capabilities

### New Capabilities

- `daily-bars-stock-universe-filter`: daily_bars 日更扫批 universe 限定为股票（`asset_type=="stock"`），排除基金/ETF/REIT 等无 TDX 日线标的对批次覆盖门槛的干扰；历史路径已如此，tip 侧对齐。
- `daily-bars-failure-granularity`: 批级 all-or-nothing 改为符号级失败归因与"合法空"豁免——一只股票无行不再废掉整批，未上市/已退市/整窗口停牌的符号不判失败，只有意外缺口才严格并走 failover。
- `daily-bars-processing-granularity`: 处理粒度开关——`[orchestrator].daily_bars_granularity`（`symbol`|`batch`，默认 `symbol`），**配置文件唯一入口、不设 CLI 参数**；`batch` 为 legacy 严格模式的精确回退；重试策略随粒度分流（symbol 精确重拉 / batch 整批重拉）。

### Modified Capabilities

<!-- 当前仓库无既有 openspec/specs，无需 delta spec。 -->

## Impact

- 修改：`src/cnequity/steps/bars.py`（`step_daily_bars` universe 预过滤 + tip 缺口重拉按符号 + 粒度分流）、`src/cnequity/orchestrator/worker_pool.py`（`_run_batch`/`_worker_fetch_batch` 按粒度分流：符号级失败归因与部分落盘 vs 整批抛错）、`src/cnequity/orchestrator/manifest.py`（failed-scope 持久化列/语义 + attempt 级批次）、`src/cnequity/config/loader.py`（`daily_bars_granularity` 字段 + 校验；**不涉及 `cli/main.py`，粒度无 CLI 参数**）、`tests/unit/test_bars_*.py` / `tests/unit/test_quality_failover.py` / `tests/unit/test_worker_manifest.py`（新增用例）。
- 不涉及：schema 主键、分区（不变）；失败缺口的 tip clist / 多日 kline failover 路由本身不变（只是触发集合从"整批"收窄到"真缺口"）。
- 副作用：日更 daily_bars 不再抓取场内基金/ETF/REIT 日线（与历史回填口径一致）；批次不再以"100 FAILED"名义完整重拉，manifest 噪声显著下降；`batch` 模式（默认关闭）提供与历史行为逐字节一致的严格回退；CLI 无 `--granularity`，切换粒度 = 改配置文件（`cne run daily`/`cne backfill`/`cne retry` 均无该参数）。
- 部署注意：datalake 需 `pip install -e .` 或重装生效；`cne config init` 或示例配置需带出 `daily_bars_granularity` 默认值。