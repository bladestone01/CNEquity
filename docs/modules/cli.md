# cli 模块

路径：`src/cnequity/cli/`

Click 命令组 `cne` 的实现（`pyproject.toml` `[project.scripts]` 与 `__main__.py` 都指向
`cnequity.cli.main:cli`）。

**完整命令与参数**见 [CLI 参考](../reference/cli.md)；入门流程见 [快速开始](../getting-started/quickstart.md)。

---

## 源码地图

命令按**做什么**分文件，不按写作顺序。`main.py` 只负责把它们 import 进来完成注册——
所以它是唯一知道整个命令面的地方，也是唯一需要改的注册点。

| 文件 | 内容 |
|------|------|
| `main.py` | 入口：import 各 `*_cmds` 模块完成注册 |
| `_root.py` | `cli` 命令组本身（放在 main 会形成循环 import） |
| `_shared.py` | `--config` 装饰器、配置解析、进度日志、退出码映射 |
| `setup_cmds.py` | `demo` `init` `config` `doctor`——有湖之前会碰到的 |
| `run_cmds.py` | `run daily` `retry` |
| `backfill_cmds.py` | `backfill` 及其分片、限定域、staging 恢复 |
| `maintain_cmds.py` | `compact` `derive` `clean` `stats` |
| `quality_cmds.py` | `audit` `verify` `status` `stability` `sources` `source` |
| `govern_cmds.py` | `contract` `profile` `snapshot`——可复现性那一面 |
| `consume_cmds.py` | `query` `serve` `mcp` |
| `delisted_cmds.py` | `delisted status` / `backfill`（重建目录在 `scripts/delisted_ops.py`） |
| `demo.py` | demo 编排 |

`--config` 由 `_shared.config_option` 统一提供：原先它被手写了 34 次，每一处都可以各自漂移。

测试打桩要指向**实际绑定该名字的模块**（`cnequity.cli.quality_cmds.JobEngine`，
不是 `cnequity.cli.main.JobEngine`）。`main.py` 刻意不再 re-export 这些内部名，
所以打错模块会直接 `AttributeError`，而不是默默给一个没人查的名字打桩。

| 关注点 | 位置 |
|--------|------|
| 配置路径解析 | `_shared.resolve_config_path`（缺省时引导 `cne config init`） |
| step 注册 | 启动时 `main.py` 里 `import cnequity.steps` |

### 退出码（供 cron / Task Scheduler）

| 场景 | 退出码 |
|------|--------|
| 成功 / `skipped_non_trading_day` | 0 |
| run / audit / init 失败 | 1 |
| `status --datasets` 有 STALE | 1 |
| `audit --full` UNHEALTHY | 1 |
