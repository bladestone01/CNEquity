# serve 模块

只读湖面板。`asl serve` 起 uvicorn，提供 `/api/*` JSON 与一个自包含页面。

| 文件 | 职责 |
|------|------|
| `app.py` | FastAPI app 工厂、pydantic 响应模型、路由、token 中间件 |
| `lake.py` | `LakeView` — 把注册表、目录布局、`meta/stats`、`meta/quality`、manifest 投影成面板要的形状 |
| `static/index.html` | 分层总览页（无构建链、无 CDN） |

---

## 两条硬边界

**只读。** 没有任何端点会跑批、重试或清理，将来也不会：一个无鉴权的本地 HTTP 服务能触发采集就是负债，而 CLI 已经是那些操作的正确入口。页面显示该跑的命令并提供复制。`test_no_route_can_mutate_the_lake` 断言路由表里只有 `GET`/`HEAD`。

唯一的例外恰好证明规则——`meta/stats` 会在后台重建，因为它是**湖的缓存**而不是湖的一部分，而一个端着上周数字的面板比一个会刷新自己缓存的面板更糟。

**不扫 curated。** 每个数值都来自已经落盘的东西。一个会读 parquet 的请求，是一个随湖增长而变慢的请求——这正是度量表存在的理由。

---

## 两个刻意不做的事

**不打开 `data/duckdb/ashare-lake.duckdb`。** DuckDB 是「多读或单写」，面板持着读句柄会让夜间跑批里的 `ensure_duckdb_views()` 拿不到写锁——面板会搞挂采集。视图在进程私有的内存库里从注册表重建，是毫秒级的。

**不重算审计 findings。** `lake_health()` 要走一遍湖；面板读 `asl audit --full` 已经写好的 `meta/quality/health-latest.json`。一次页面访问不该花一次审计的代价——代价是显示的是上次审计的快照，页面上标了日期。

---

## 端点

| 端点 | 内容 |
|------|------|
| `GET /api/health` | 锚定交易日、fresh/stale/empty 计数、总行数与体积、findings 分级、度量表新鲜度 |
| `GET /api/tiers` | L0–L8 汇总（数据集数、各状态计数、行数、体积、成员） |
| `GET /api/datasets?tier=` | 逐数据集：注册表字段 + 覆盖 + 水位 + 度量 |
| `GET /api/datasets/{name}` | 单数据集详情：注册表契约 + schema + 主键 + 缺口 + findings + 建议命令 + 最近 batch |
| `GET /api/datasets/{name}/partitions` | 逐分区行数与体积序列 |
| `GET /api/datasets/{name}/provenance` | source × data_version 合计与 `fetched_at` 跨度 |
| `GET /api/datasets/{name}/provenance/series` | 同上但按时间分桶——source 分布**何时**变的 |
| `GET /api/heatmap?days=` | 数据集 × 交易日覆盖网格 |
| `GET /api/docs` | OpenAPI 页，由 handler 生成，不会与实现漂移 |

`empty` 拆成 `empty_optional` / `empty_required`：一个没人开启的可选数据集和一个失败的必需数据集在磁盘上长得一模一样，混在一起报会让人学会忽略它。

### 热力图的诚实性

格子回答的是「**存不存在一个覆盖这天的分区**」。对月/季/年分区的数据集，这比它画在上面的那一天要粗——目录覆盖的是整个周期，某一场具体交易日在里面有没有行，不读文件是不知道的。`granularity` 随每行返回，渲染层据此说明，而不是暗示一个布局并不具备的精度。

每行还带 `cadence_days`（即 `max_staleness_days`）。缺口只对**日更**的源意味着落后：`northbound_holdings` 是季频的，它跨度内绝大多数交易日本来就没有分区，把那些画成故障会让每一行都在喊狼来了。页面据此把非日更源的间隔渲染成中性色。

单元格字母表：`#` 有覆盖、`.` 缺口、空格 覆盖区间外、`-` 无分区（单文件 merge）。一行一个字符串而不是一万个 JSON 对象。

### 详情页的两个 tab

**状态**：覆盖条（含源端视野天花板）、缺口、溯源堆叠图、溯源合计表、审计 findings、最近 batch。
**元数据**：契约（分层 / 分区键 / 粒度 / 主键 / schema）、语义（`fetch_semantics` / `history_mode` / PIT / 水位）、来源（回填源 / 视野 / 最早可得）、运维（容忍天数 / required / 分块 / 日内频率）、可复制的命令。

全部来自 `domain/datasets.py` 与 `domain/schemas.py`——面板不复制一份。

**缺口按数据集自己的周期计数**，不按天：一个年分区的数据集不会因为一个目录覆盖整年就"缺 364 天"，那样报会把真缺口淹掉。日粒度只算交易日——周末不是缺口。计数同样带 `max_staleness_days` 语境：`northbound_holdings` 是季频的，59 个交易日无分区属其节奏，页面用中性色而不是红色。

### 两个不内联的东西

`partitions_detail` 不进详情响应：daily_bars 一个就有 6202 条，而详情在每次切 tab 时都要加载，逐分区序列只有一张图用得上。走 `/partitions`。

溯源序列**服务端分桶**：daily_bars 的 (日, source) 点有 11,324 个，一兆 JSON 画几百像素。桶宽逐级放大到序列装得下，并把选中的宽度（`bucket`）随响应返回——不告诉调用方它在看月度数据，坐标轴就没法诚实标注。

### 图表

分类色板取自 dataviz 参考实例的前 5 槽，两个模式都跑过校验器（最差相邻 CVD ΔE 9.1 light / 8.4 dark，正常视觉 19.6 / 19.3）。light 模式有三个槽低于 3:1 对比度，所以**图例与图下的合计表是必需的**，不是装饰。

source 按名称字母序占槽，不按行数排名——一个源恰好长大了不该让整张图重新上色。超过 5 个折进中性色的「其他」，不循环取色。

---

## 鉴权与绑定

默认绑 `127.0.0.1`。`--host` 指向非回环地址时**必须**给 `--token`，否则拒绝启动——这个检查排在读配置之前，免得 `--config` 打错字先失败、把绑定守卫盖掉。

token 支持 `Authorization: Bearer <token>` 与 `?token=`（页面用后者：浏览器发不了自定义头）。

---

## 缓存

`LakeView` 对目录遍历结果做 30 秒 TTL 缓存——总览页一次扇出好几个端点，否则每个都要重走一遍。缓存构建在锁外进行：两个并发 miss 各做一次，比让每个请求排在一次目录遍历后面便宜。

后台刷新的线程策略在这里，不在 `storage.stats`——那个模块保持同步、可测试。同一时刻只跑一个：stats 自己的锁本来就会收敛重复，但每个请求起一个线程再立刻丢锁是纯浪费。

---

## 前端

页面是手写 HTML + vanilla JS + CSS grid，**没有构建链、没有 CDN、没有外部字体**：湖经常跑在离线机器或需要代理的网络里，那里的一个外部资源就是一个打不开的页面。`test_the_page_is_served_and_self_contained` 断言页面里不出现任何外链。

图表库（缩放、下钻的热力图）是下一步的事；在那之前 CSS grid 渲染同样的格子，而这个文件还是能读的。

---

## 相关文档

- [CLI 参考 · asl serve](../reference/cli.md#asl-serve)
- [storage 模块 · stats.py](storage.md#statspy)
