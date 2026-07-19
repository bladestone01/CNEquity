# 故障排查

按症状分类的处置指南。原则：**先定位 run_id 与失败 batch，再 retry，最后 audit 复核**。

---

## 症状：baostock / 免费源「黑名单」或频繁失败

| 现象 | 原因 | 处理 |
|------|------|------|
| `baostock login failed: 黑名单用户` | IP/账号被免费 API 封禁（全市场扫太快） | **停扫**；换出口或等解封（常数小时～数天）。解封后用 `[sources.baostock]` 默认限速 resume，勿提高并发 |
| 东财 429 / Empty reply / 连接被掐 | 请求过密或海外出口 | 保持 `min_interval_seconds ≥ 1.0`；大陆出口或 `proxy`；见下文 sector_bars |
| cninfo / akshare 间歇失败 | 同源风控 | 已按页/按调用 `rate_limit`；失败 fail-loud 后降频重试 |

原则：**时间可以等，封禁成本远高于多等一天。** 勿为加速关掉 `min_interval` 或开多进程打同一免费源。

---

## 症状：load() 读不到新数据

| 可能原因 | 检查 | 处理 |
|----------|------|------|
| 数据仍在 staging | `ls staging/*/run_id=*` | `asl compact --run-id <id>` 或 `asl retry` |
| 分组 run 未 compact | 组 steps 是否含 `compact` | 配置修正后重跑组 |
| compact 被 gate 跳过 | `asl status` 看 failed batch | `asl retry --run-id <id>` |
| 路径错误 | `config.data_root` | 核对 `configs/ashare-lake.toml` |

---

## 症状：asl run daily 失败

1. `asl status` 查看 `run_summary` 与 failed batches
2. 查看日志 `error_message`（TDX 断连、HTTP 429、schema 校验失败等）
3. `asl retry --run-id <id>`
4. 若 TDX 问题：`asl servers test`；换 `[tdx_protocol.hosts].standard`
5. 若单数据集持续失败：`asl backfill <dataset>`（需支持 backfill）

### 周末 / 漏跑后水位落后

- **现象**：今天非交易日时 `asl run daily` → `skipped_non_trading_day`；`daily_bars`
  水位停在上上个交易日；下游 freshness 门禁不过。
- **处理**（补 core + `market_breadth`）：

  ```bash
  uv run asl run catchup                      # 默认：最近一个交易日
  uv run asl run catchup --trade-date 2026-07-17
  # 国内出口再补 capital/research（东财失败不挡门禁 exit 0）：
  uv run asl run catchup --trade-date 2026-07-17 --all-groups
  # 或分步：
  uv run asl run daily --group core --trade-date 2026-07-17
  ```

  全组补跑：`scripts/daily_pipeline.sh 2026-07-17`（或 `ASL_TRADE_DATE=...`）。
  **不要**对漏跑日随便加 `--backfill`：东财 CA 全量扫描在海外常直接失败。
  **海外机器**：catchup（TDX core + 本地 derive breadth）通常够用；
  `fund_flow` / `hot_rank` / `sector_bars` 日更落后属预期，等国内出口再 `--all-groups`。

### 云主机 / SOCKS 能开 ipinfo 但东财 Empty reply

- **现象**：出口 IP 显示 CN，`curl`/`ssh -D` 访问 `push2his` 仍 `Empty reply` /
  `Failure when receiving data from the peer`；同机 RDP 里直接 `curl` 有时也曾成功后被掐。
- **原因**：经 SOCKS 时 TLS 仍在境外客户端握手；云 IP 也易被东财短时风控。
- **处理**：在**本机进程**发起请求的大陆机器上跑回填（不要指望 Mac→云 SOCKS）；
  探针失败就停，换宽带出口或等解封后再 `sector_bars --force`。

### sector_bars backfill 大量失败

- **现象**：日志 `sector kline failed for BKxxxx on all push2his hosts`；`failed_sectors` 接近 991。
- **原因**：`push2his.eastmoney.com` 在海外 IP 常不可用；日更 clist 仍可能正常。
- **处理**：在大陆出口或 `HTTPS_PROXY` / `[sources.eastmoney] proxy` 下
  `asl backfill sector_bars --retry-failed`；全量换源后 `--force`。
  Checkpoint：`meta/state/sector_bars_backfill.json`。

### 海外机器 + 国内阿里云 VPS（推荐跑在 VPS 上）

东财 HTTPS 可用本机 `proxy` / `HTTPS_PROXY`；**baostock ST 回填是自有 TCP，普通 HTTP 代理无效**。
最稳做法：把引擎（或至少 `data.root`）同步到阿里云，在 VPS 上跑一键脚本：

```bash
# 本机 → VPS（示例）
rsync -avz --progress ~/code/ashare-lake/ user@VPS:~/ashare-lake/

# VPS 上
cd ~/ashare-lake && uv sync
./scripts/china_egress_backfill.sh          # sector_bars --force + trading_status ST
# ./scripts/china_egress_backfill.sh --sector-only
# ./scripts/china_egress_backfill.sh --st-only   # ST 可断点续跑

# 跑完把湖同步回本机
rsync -avz --progress user@VPS:~/ashare-lake/data/ashare-lake/ \
  ~/code/ashare-lake/data/ashare-lake/
```

安全组只开 **你自己的 IP → 22**；不要把代理端口对公网开放。
本机隧道备选：`ssh -D 7890`（仅东财）或 `sshuttle` / `proxychains`（才可能带上 baostock）。

---

## 症状：audit --full UNHEALTHY

| Finding 类型 | 含义 | 处理 |
|--------------|------|------|
| `pk_duplicate` | curated PK 重复 | 查最近 compact；必要时 backfill 重跑该分区 |
| `mock_rows` | 生产环境 mock 数据 | 关闭 `allow_mock`；清 mock 分区重采 |
| `adj_close_discontinuity` | 复权收益异常 | `asl derive adj_factors`；查 Sina 源 |
| `missing_corporate_action` | 除权日无 corp action | `asl backfill corporate_actions` |
| `trading_status_coverage_start` | ST 覆盖起点晚 | 预期警告；跑 baostock ST 回填 |
| `partition_row_count_mutation` | 行数突变 | 查是否误 compact 或源口径变化 |

Findings 文件：`meta/quality/findings/{run_id}.json`

---

## 症状：status --datasets STALE

1. 确认最近交易日是否跑过 pipeline
2. 查该数据集最近成功 run：`asl status`
3. 重跑对应 group：`asl run daily --group <name>`
4. 季频数据集（`northbound_holdings`）容忍 100 天 — 非故障

`is_stale()` 逻辑：`domain/datasets.py`

---

## 症状：init 中断

**禁止**直接重新 `asl init`（会拒绝或产生冲突）。

```bash
asl init --resume
# 或
asl retry --run-id <init_run_id>
```

`--keep-going`：单 phase 失败后继续后续 phase（用于尽量多回填）。

---

## 症状：RunLockError

另一 `retry`/`compact` 正在持有锁。等待或删除陈旧锁：

```
meta/locks/
```

仅确认无活跃 asl 进程后手动清理。

---

## 症状：磁盘不足

1. `asl clean` 清理已 compact 的 staging
2. 压缩或归档旧 `meta/source_snapshots/`（长期会膨胀）
3. curated 勿删；用 backfill 重采而非部分删除

---

## 症状：复权因子大量缺失

```bash
asl derive adj_factors
asl audit --full
```

`strict_adj=True` 时缺因子会报错 — 检查 `derived/adj_factors` 覆盖与 `adj_factors_cache`。

---

## 诊断命令速查

```bash
asl config validate
asl servers test
asl status
asl status --datasets
asl catalog
asl audit --full
asl retry --run-id <id>
asl clean --dry-run
```

---

## 相关文档

- [运维 Runbook](runbook.md)
- [数据流](../architecture/data-flow.md)
- [运维 Runbook](runbook.md)
