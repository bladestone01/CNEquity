# baostock 适配器

路径：`src/cnequity/adapters/baostock/`

[Baostock](http://baostock.com) 开源证券数据接口。用于 **历史回填** 场景，非日更主源。

**依赖**：随 `pip install cnequity` / 可编辑安装一并提供，无需额外 extras。

---

## 文件

| 文件 | 职责 |
|------|------|
| `_session.py` | 登录/重登、逐 symbol 拉取驱动 |
| `corporate_actions.py` | 受控的沪深退市股分红除权修复 → corporate_actions |
| `valuation.py` | 历史 PE/PB/PS 等 → valuation_metrics |
| `st_history.py` | K 线 `isST` 标记 → trading_status ST 历史 |
| `__init__.py` | 导出 |

---

## valuation.py

- `valuation_metrics` 的 `DatasetSpec.backfill_source = "baostock"`
- `cne backfill valuation_metrics` 走此路径
- 日更仍用东财快照

---

## st_history.py

- 从 baostock 日 K 的 `isST` 字段推断历史 ST
- 由 `reference` / trading_status 相关 step 在 backfill 模式调用

BJ 历史 ST 不由 Baostock 提供；可选的 Tushare Pro `bak_basic`（2016）+ `stock_st`
（2017-01-01 起）适配器位于 `adapters/tushare/st_history.py`。两者都必须输出显式的
`normal` 负证据，空响应不能直接视作非 ST。
- 补充 EastMoney 无法提供的历史 ST

## corporate_actions.py

- 不参与日更，也不改变默认 `cne backfill corporate_actions` 的 TDX 路径
- 通过 `cne backfill corporate_actions --baostock-repair` 显式启用
- 只对 instruments 中已退市的 SH/SZ 标的请求 `query_dividend_data`
- 每票按 `list_date..delist_date` 裁剪年份窗口，避免查询上市前/退市后的无效年份
- corporate_actions 修复默认跟随项目研究底 `2001-01-01`；需要更窄范围时通过 `--start/--end` 明确指定
- `yearType="operate"` 的 `dividOperateDate` 作为除权除息日；现金、送股、转股按每股单位拆成独立行
- 北交所代码会被 Baostock 接口拒绝，因此继续作为 `missing_corporate_action_delisted` 的已知源限制
- Baostock 该接口不提供可靠的配股比例/价格，配股仍依赖 TDX/EastMoney

该开关建议与 `--symbols` 一起使用先做小范围修复，例如：

```bash
cne backfill corporate_actions --start 2020-01-01 --end 2020-12-31 \
  --symbols 300114.SZ --baostock-repair
```

---

## 会话管理

baostock 需匿名 `bs.login()`；`_session.py` 处理：

- 单进程内复用 session；周期性 relogin；socket/watchdog 防挂死
- 断线重登 + 逐 symbol 重试；失败 symbol 返回给调用方（fail-loud / 可 resume）

### 防黑名单限速（必开）

官方免费 API 限制（超限进入黑名单，`error_code=10001011`）：

- **每日 API 请求不能超过 5 万次**
- **不能并发连接访问**（单连接串行）

全市场历史回填极易触发「黑名单用户」。配置见 `[sources.baostock]`：

| 键 | 默认 | 说明 |
|----|------|------|
| `min_interval_seconds` | 1.0 | 每 symbol 前跨进程限速（`config.rate_limit("baostock")`） |
| `batch_size` | 20 | 每完成 N 个 symbol 额外休息 |
| `batch_rest_seconds` | 120 | 批次间冷却秒数 |

时间可以慢：~5000 票 ×（1s + 批次休息）是**有意**的，换 IP 解封后务必用该配置 resume，**勿开多进程/多连接并行扫 baostock**。

---

## 相关文档

- [datasets — valuation_metrics](../../datasets/catalog.md)
- [查询指南 — 历史 ST](../../datasets/query-guide.md)
- [故障排查 — 数据源封禁](../../operations/troubleshooting.md)
