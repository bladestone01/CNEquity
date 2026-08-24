# 20260822-instruments-master

## 结论

`instruments` 是证券主数据（security master）：每证券一行的权威快照（symbol/name/exchange/asset_type/list_date/delist_date/prev_symbol + source/data_version/fetched_at），是 universe、归属豁免、幸存者修复、估值对账的地基；多源补齐（TDX + EastMoney list_date + baostock ipoDate/退市 + Sina BJ + delisted 目录），compact 为 merge-style 且保留退市名。

核心排查坑：`list_date` 为 null 时"未上市 / 上市未交易"的豁免全部失效，新股会持续 failed（表现为每次 retry 都是 `-attempt-N`）；`list_date` 语义还有"挂牌日 vs 首个交易日"的口径差异。

## 证据/出处

- schema：`src/cnequity/domain/schemas.py:131` `INSTRUMENTS_SCHEMA`（字段/类型见该处）
- 写入 step：`src/cnequity/steps/reference.py:61` `step_instruments`（TDX security list 主抓 + `_merge_untdxable_instruments` 补 BJ）
- baostock 全量（退市 + ipoDate→list_date）：`reference.py:120` `_merge_delisted_instruments`（`cne backfill instruments` 触发）
- EastMoney list_date 补齐：`src/cnequity/adapters/eastmoney/instruments.py:61` `enrich_instrument_list_dates`
- 退市目录/身份证据/修复：`src/cnequity/steps/delisted.py`（discover / reconcile / repair / identity evidence）
- 合并与防误判：`src/cnequity/storage/instruments.py` `compact_instruments`（保留退市名、部分抓取缺席不得推断 delist）
- 实测：603448.SH / 301688.SZ 等新股 `list_date=null` → 豁免失效 → 持续 `-attempt-N` 失败；新浪独立探测 08-16..08-21 窗口内 0 行，判定为"未开启交易"而非 symbol 异常

## 如何填充 instruments（获取入口与优先级）

多源 merge，`compact_instruments` 统一合并（merge-style、保留退市名、防"单次部分抓取缺席"误判 delist）。各入口：

1. **`step_instruments`（日更 core，reference.py:61）——日常主入口**
   每次 `cne run daily` 触发：TDX security list 主抓（SH/SZ：symbol/name/exchange/asset_type）→ `enrich_instrument_list_dates`（EastMoney 补 `list_date`）→ `_merge_untdxable_instruments`（delisted 目录的 live-but-missing 桶补 BJ 等，source=sina）。
2. **`cne backfill instruments`（`--backfill` 分支，reference.py:120）——补 `list_date`/退市主入口**
   `_merge_delisted_instruments`：baostock `query_stock_basic` 全量 → 退市名 + `ipoDate→list_date`。新股 `list_date=null` 时优先跑这个。
3. **EastMoney `list_date` 补齐**（`eastmoney/instruments.py:61 fetch_list_date_map`）——上市日/首个交易日的另一权威源；随日更自动做，依赖 `[sources].eastmoney.enabled`。
4. **baostock 退市全量身份**（`write_delisted_identity_evidence`）——`query_stock_basic` 成功写一次 → `known_delisted_instruments`（正式身份证据）。
5. **delisted 目录**（`cne delisted discover`，Sina 码空间扫描）→ `delisted_catalog.json`（delisted / never_issued / live-but-missing 三态），live-but-missing 供（2）之外的 BJ 补单；`reconcile_delisted_catalog` 校验退市日、`repair_delisted_instruments` 从湖内 bar 推导 list/delist 写回。
6. **落库**：`compact_instruments` 合并 staging→curated（保留退市名、防"缺席 live"误判 delist）。

配置依赖：`[sources].eastmoney.enabled`（list_date 补齐）、`[sources].baostock.enabled`（ipoDate/退市，init/backfill 用）。

实操（新股 `list_date=null`）：`cne backfill instruments`（baostock ipoDate）→ 未覆盖再用 EastMoney list_date；补齐后 `list_date > end` 自动判为 `expected_no_data`，不再持续失败。

## 状态: promising