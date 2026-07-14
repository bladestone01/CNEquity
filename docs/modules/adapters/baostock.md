# baostock 适配器

路径：`src/stock_data_engine/adapters/baostock/`

[Baostock](http://baostock.com) 开源证券数据接口。用于 **历史回填** 场景，非日更主源。

**依赖**：`pip install -e ".[valuation]"`（extra 名 `valuation`）

---

## 文件

| 文件 | 职责 |
|------|------|
| `_session.py` | 登录/重登、逐 symbol 拉取驱动 |
| `valuation.py` | 历史 PE/PB/PS 等 → valuation_metrics |
| `st_history.py` | K 线 `isST` 标记 → trading_status ST 历史 |
| `__init__.py` | 导出 |

---

## valuation.py

- `DatasetSpec.backfill_source = "baostock"` for `valuation_metrics`
- `sde backfill valuation_metrics` 走此路径
- 日更仍用 EastMoney 快照

---

## st_history.py

- 从 baostock 日 K 的 `isST` 字段推断历史 ST
- 由 `reference` / trading_status 相关 step 在 backfill 模式调用
- 补充 EastMoney 无法提供的历史 ST

---

## 会话管理

baostock 需 `bs.login()`；`_session.py` 处理：

- 单进程内复用 session
- 断线重登
- 限速配合 `[sources]` 配置

---

## 相关文档

- [datasets — valuation_metrics](../../datasets/catalog.md)
- [查询指南 — 历史 ST](../../datasets/query-guide.md)
