# akshare 适配器

路径：`src/ashare_lake/adapters/akshare/`

[AKShare](https://github.com/akfamily/akshare) 开源财经数据接口，作 **补充源**。

**依赖**：`pip install -e ".[macro]"`（宏观指标场景）

---

## 文件

| 文件 | 用途 |
|------|------|
| `trading_status.py` | 补充 ST symbol 集合，与 EM 合并 |
| `__init__.py` | 导出 |

---

## trading_status

EastMoney ST 列表偶发不全时，akshare 提供第二路 ST 标记集合，step 层做 union。

---

## macro

`adapters/macro/indicators.py` 在 EastMoney 缺指标时 fallback akshare（PMI、M2、社融等月度序列）。

---

## 配置

```toml
[sources.akshare]
enabled = true
# Often wraps EastMoney — keep ≥ EM interval.
min_interval_seconds = 1.0
```

---

## 相关文档

- [macro 适配器](macro.md)
- [reference step](../steps.md)
