# mofcom 适配器

路径：`src/ashare_lake/adapters/mofcom/`

商务部数据中心，为 `macro_indicators` 提供 **社会融资规模增量**（`social_financing`）。

来源页：<https://data.mofcom.gov.cn/gnmy/shrzgm.shtml>

---

## 文件

| 文件 | 用途 |
|------|------|
| `social_financing.py` | 月度社融增量，返回 `[{obs_date, value}]` |
| `__init__.py` | 导出 |

---

## 为什么直连而不用 AkShare 包装

原先这条序列走 `akshare.macro_china_shrzgm`。两个问题：

1. **按位置重命名列。** 包装层把一组固定的中文列名依次赋给 MOFCOM 返回的
   任意键序——键序一变，社融就会被贴上「委托贷款」的标签，而且不会报错。
   实际响应是有名字的（`tiosfs` / `rmblaon` / `entrustloan` …），按键读取
   从根本上没有这种失效模式。
2. **月份格式对不上。** 包装层输出紧凑的 `YYYYMM`（`202604`），而 macro
   适配器的日期解析只认带分隔符的写法，于是每一行社融都被静默丢弃——
   这个指标在配置和文档里都存在，却从未写入过 curated 一行。

见 [issue #3](https://github.com/rootSunc/ashare-lake/issues/3)。

---

## 数值正确，但落后约两个发布周期

社会融资规模是**央行**的统计口径，商务部只是转载。2026-08-01 实测：

- 数值一致：本适配器 2026 年前四月累计 154 507 亿，央行公布 15.45 万亿（差额为取整）
- **时效落后**：MOFCOM 最新月份是 2026-04，而央行已在 2026-07-15 发布了 6 月数据

对一条月度宏观序列来说这个滞后可以接受，但它是真实存在的，所以
`quality/macro_checks.py` 给 `social_financing` 单独放宽了陈旧阈值（135 天），
既容忍常态滞后，又能在再错过一个发布周期时告警。

改直连央行可以拿到更新的数据，代价是央行只发 HTML 稿、没有稳定的 JSON 接口，
解析层比现在脆弱。本适配器暂不改；若时效成为问题，这是明确的下一步。

---

## 行为

- 取全量已发布序列（2015-01 至今，约 136 个月），按 `obs_date <= trade_date` 过滤
- 观测日期落在**月末**，与其他月度指标一致
- 网络或结构异常降级为空列表 + WARN：这是多源数据集里的一个指标，
  不该让日更整体失败；下次运行取全量，缺口自动补回
- 用普通 `httpx`，不做 Chrome TLS 伪装——该端点在伪装握手下会挂起

## 配置

```toml
[sources.mofcom]
enabled = true
min_interval_seconds = 1.0
```

`enabled = false` 时跳过；缺省视为开启（它是该指标的主源，不是补充源）。

---

## 相关文档

- [macro 适配器](macro.md)
- [macro_risk step](../steps.md)
