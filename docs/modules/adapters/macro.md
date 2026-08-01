# macro 适配器

路径：`src/ashare_lake/adapters/macro/`

宏观经济指标采集，写入 `macro_indicators` 数据集。

---

## 文件

| 文件 | 职责 |
|------|------|
| `indicators.py` | 多指标拉取、EM 主 + akshare 备 |
| `__init__.py` | 导出 |

---

## 指标示例

| indicator_id | 说明 | 频率 | source |
|--------------|------|------|--------|
| `cnbond_yield_10y`、`shibor_3m`、`lpr_1y` | 利率曲线 | 日 / 月 | `eastmoney` |
| `pmi_manufacturing`、`m2_yoy`、`social_financing` | 景气/货币 | 月 | `akshare` |

具体 ID 列表见 `indicators.py` 内注册与 [schema.md](../../datasets/schema.md)。

---

## 溯源

行级 `source` 由适配器写入，不取 step 的统一值：东财直连行为 `eastmoney`，
akshare 补充行为 `akshare`。`with_provenance` 只在缺列时填充，故适配器的标注会保留。

akshare 段落受 `[sources.akshare].enabled` 控制，关闭（或配置中无此段落）时
月度序列为空，日频利率不受影响。

---

## 分区与主键

- 分区：`obs_date`
- 主键：`(indicator_id, obs_date)`

---

## 相关文档

- [macro_risk step](../steps.md)
- [datasets — L6](../../datasets/catalog.md)
