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

| indicator_id | 说明 | 频率 |
|--------------|------|------|
| 国债收益率、SHIBOR 等 | 利率曲线 | 日/周 |
| PMI、M2、社融 等 | 景气/货币 | 月（akshare 补充） |

具体 ID 列表见 `indicators.py` 内注册与 [schema.md](../../datasets/schema.md)。

---

## 分区与主键

- 分区：`obs_date`
- 主键：`(indicator_id, obs_date)`

---

## 相关文档

- [macro_risk step](../steps.md)
- [datasets — L6](../../datasets/catalog.md)
