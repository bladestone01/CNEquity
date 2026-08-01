# macro 适配器

路径：`src/ashare_lake/adapters/macro/`

宏观经济指标采集，写入 `macro_indicators` 数据集。

---

## 文件

| 文件 | 职责 |
|------|------|
| `indicators.py` | 日频利率 + 月度序列，各自直连发布方 |
| `__init__.py` | 导出 |

---

## 指标

| indicator_id | 说明 | 频率 | source | 上游 |
|--------------|------|------|--------|------|
| `cnbond_yield_10y` | 10 年期国债收益率 | 日 | `eastmoney` | `RPTA_WEB_TREASURYYIELD` |
| `shibor_3m` | 3 个月 SHIBOR | 日 | `eastmoney` | `RPT_IMP_INTRESTRATEN` |
| `lpr_1y` | 1 年期 LPR | 月 | `eastmoney` | `RPTA_WEB_RATE` |
| `pmi_manufacturing` | 制造业 PMI | 月 | `eastmoney` | `RPT_ECONOMY_PMI` |
| `m2_yoy` | M2 同比增长（%） | 月 | `eastmoney` | `RPT_ECONOMY_CURRENCY_SUPPLY` |
| `social_financing` | 社会融资规模增量 | 月 | `mofcom` | [mofcom 适配器](mofcom.md) |

PMI 与 M2 曾经走 AkShare。它的两个包装函数请求的正是上表里同一个东财
datacenter 端点，所以直连没有换发布方，只是去掉了一层解析，并接上本项目自己的
重试 / 限速 / TLS 处理。见 [issue #3](https://github.com/rootSunc/ashare-lake/issues/3)。

> `m2_yoy` 必须读 `BASIC_CURRENCY_SAME`。AkShare 路径用中文列名做子串匹配、
> 匹配不上时回落到「最后一列」，而它的关键词 `M2-同比增长` 从来匹配不上真实列名
> `货币和准货币(M2)-同比增长`（括号断开了子串），于是长期把
> **M0 环比增长**写成了 `m2_yoy`。按字段名读取不会有这种失效模式。

具体 ID 列表见 `indicators.py` 内注册与 [schema.md](../../datasets/schema.md)。

---

## 溯源

行级 `source` 由适配器写入，不取 step 的统一值：东财行为 `eastmoney`，
社融行为 `mofcom`。`with_provenance` 只在缺列时填充，故适配器的标注会保留。

---

## 观测日期

月度指标统一落在**月末**。东财的 `REPORT_DATE` 是月初（`2026-07-01`），会转换；
改动这个约定会让 curated 里已有的每个月份多出一把主键。

---

## 分区与主键

- 分区：`obs_date`
- 主键：`(indicator_id, obs_date)`

---

## 相关文档

- [macro_risk step](../steps.md)
- [datasets — L6](../../datasets/catalog.md)
