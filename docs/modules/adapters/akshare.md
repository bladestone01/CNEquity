# akshare 适配器

路径：`src/ashare_lake/adapters/akshare/`

[AKShare](https://github.com/akfamily/akshare) 开源财经数据接口，作 **补充源**。

**依赖**：随 `pip install ashare-lake` / 可编辑安装一并提供，无需额外 extras。

---

## 文件

| 文件 | 用途 |
|------|------|
| `trading_status.py` | 补充 ST symbol 集合，与 EM 合并 |
| `__init__.py` | 导出 |

---

## trading_status

EastMoney ST 列表偶发不全时，akshare 提供第二路 ST 标记集合，step 层做 union。

> 注意：`ak.stock_zh_a_st_em` 请求的是 `push2.eastmoney.com` 的 clist 接口、
> `fs=m:0+f:4,m:1+f:4`，与本项目 `adapters/eastmoney/trading_status.py` 的查询完全相同。
> 因此这不是独立口径的交叉校验，只相当于换一个 host 分片重试一次；
> 真正独立的 ST 口径来自 baostock 的 `isST`（见 `adapters/baostock/st_history.py`）。

---

## macro

`adapters/macro/indicators.py` 在 EastMoney 缺指标时 fallback akshare（PMI、M2、社融等月度序列）。

产出的行带 `source = "akshare"`（不是 step 层的 `eastmoney`），curated 中可与东财行区分。
`lpr_1y` 始终优先东财直连，不走 akshare。

---

## 配置

```toml
[sources.akshare]
enabled = true
# 常包装东财接口 — 间隔建议 ≥ 东财配置
min_interval_seconds = 1.0
```

两个调用点（ST union、月度宏观）都检查这个开关；
配置中没有 `[sources.akshare]` 段落时按关闭处理，akshare 不会被 import。

---

## 相关文档

- [macro 适配器](macro.md)
- [reference step](../steps.md)
