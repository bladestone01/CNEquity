# 故障排查

按症状分类的处置指南。原则：**先定位 run_id 与失败 batch，再 retry，最后 audit 复核**。

---

## 症状：load() 读不到新数据

| 可能原因 | 检查 | 处理 |
|----------|------|------|
| 数据仍在 staging | `ls staging/*/run_id=*` | `sde compact --run-id <id>` 或 `sde retry` |
| 分组 run 未 compact | 组 steps 是否含 `compact` | 配置修正后重跑组 |
| compact 被 gate 跳过 | `sde status` 看 failed batch | `sde retry --run-id <id>` |
| 路径错误 | `config.data_root` | 核对 `configs/stockdata.toml` |

---

## 症状：sde run daily 失败

1. `sde status` 查看 `run_summary` 与 failed batches
2. 查看日志 `error_message`（TDX 断连、HTTP 429、schema 校验失败等）
3. `sde retry --run-id <id>`
4. 若 TDX 问题：`sde servers test`；换 `[tdx_protocol.hosts].standard`
5. 若单数据集持续失败：`sde backfill <dataset>`（需支持 backfill）

### sector_bars backfill 大量失败

- **现象**：日志 `sector kline failed for BKxxxx on all push2his hosts`；`failed_sectors` 接近 991。
- **原因**：`push2his.eastmoney.com` 在海外 IP 常不可用；日更 clist 仍可能正常。
- **处理**：在大陆出口或 `HTTPS_PROXY` 下 `sde backfill sector_bars --retry-failed`；全量换源后 `--force`。Checkpoint：`meta/state/sector_bars_backfill.json`。

---

## 症状：audit --full UNHEALTHY

| Finding 类型 | 含义 | 处理 |
|--------------|------|------|
| `pk_duplicate` | curated PK 重复 | 查最近 compact；必要时 backfill 重跑该分区 |
| `mock_rows` | 生产环境 mock 数据 | 关闭 `allow_mock`；清 mock 分区重采 |
| `adj_close_discontinuity` | 复权收益异常 | `sde derive adj_factors`；查 Sina 源；见 G1 |
| `missing_corporate_action` | 除权日无 corp action | `sde backfill corporate_actions` |
| `trading_status_coverage_start` | ST 覆盖起点晚 | 预期警告；跑 baostock ST 回填 |
| `partition_row_count_mutation` | 行数突变 | 查是否误 compact 或源口径变化 |

Findings 文件：`meta/quality/findings/{run_id}.json`

---

## 症状：status --datasets STALE

1. 确认最近交易日是否跑过 pipeline
2. 查该数据集最近成功 run：`sde status`
3. 重跑对应 group：`sde run daily --group <name>`
4. 季频数据集（`northbound_holdings`）容忍 100 天 — 非故障

`is_stale()` 逻辑：`domain/datasets.py`

---

## 症状：init 中断

**禁止**直接重新 `sde init`（会拒绝或产生冲突）。

```bash
sde init --resume
# 或
sde retry --run-id <init_run_id>
```

`--keep-going`：单 phase 失败后继续后续 phase（用于尽量多回填）。

---

## 症状：RunLockError

另一 `retry`/`compact` 正在持有锁。等待或删除陈旧锁：

```
meta/locks/
```

仅确认无活跃 sde 进程后手动清理。

---

## 症状：磁盘不足

1. `sde clean` 清理已 compact 的 staging
2. 压缩或归档旧 `meta/source_snapshots/`（长期会膨胀，G7）
3. curated 勿删；用 backfill 重采而非部分删除

---

## 症状：复权因子大量缺失

```bash
sde derive adj_factors
sde audit --full
```

`strict_adj=True` 时缺因子会报错 — 检查 `derived/adj_factors` 覆盖与 `adj_factors_cache`。

---

## 诊断命令速查

```bash
sde config validate
sde servers test
sde status
sde status --datasets
sde catalog
sde audit --full
sde retry --run-id <id>
sde clean --dry-run
```

---

## 相关文档

- [运维 Runbook](runbook.md)
- [数据流](../architecture/data-flow.md)
- [PRD 风险登记](../PRD.md)
