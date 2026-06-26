# Operations Guide

## Directory layout

After `sde init`:

```
{data.root}/
  staging/
  curated/
  derived/
  meta/manifest.db
  meta/quality/
  meta/source_snapshots/
  meta/on_demand/
  duckdb/stockdata.duckdb
```

## T+1 daily schedule (cron example)

```cron
# Core reference + bars + derive (Mon-Fri 16:05)
5 16 * * 1-5 cd /path/to/StockDataEngine && sde run daily --group core --config configs/stockdata.example.toml

# Capital tables (16:35)
35 16 * * 1-5 sde run daily --group capital --config configs/stockdata.example.toml

# Signals (17:05)
5 17 * * 1-5 sde run daily --group signals --config configs/stockdata.example.toml
```

## Init phases

```bash
sde init --config configs/stockdata.example.toml
```

Runs phases in order; Phase 2c (daily_bars backfill) may take 15–20 minutes.

## Failure recovery

```bash
sde status --config configs/stockdata.example.toml
sde retry --run-id <id> --config configs/stockdata.example.toml
```

Only failed batches are re-executed; successful batches are skipped.

## Audit

```bash
sde audit --config configs/stockdata.example.toml
```

Writes findings to `meta/quality/findings/{run_id}.json`. Cross-source diffs go to `meta/quality/source_diffs/`. **No automatic source switching.**

## Backup

```bash
tar czf backup-$(date +%Y%m%d).tar.gz data/stock-data-engine/curated data/stock-data-engine/meta
cp data/stock-data-engine/duckdb/stockdata.duckdb backup/
```

## Source failover policy

1. Primary source fails → batch retry with backoff (max 3).
2. Still failing → mark batch failed; optional backup fetch writes to `meta/source_snapshots`.
3. `sde audit` compares primary vs snapshot; human decides source switch.
4. Never silently overwrite curated canonical rows from backup.

## EastMoney HTTP

Engine applies NID auth patch at startup (`adapters/eastmoney/em_auth.py`). Ensure outbound HTTPS to `*.eastmoney.com`.

## Docker (optional)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[tdx]"
CMD ["sde", "run", "daily", "--config", "configs/stockdata.example.toml"]
```

Mount `{data.root}` as a volume for persistence.

## Monitoring

- `sde status`: latest run, batch counts, failed batches
- manifest.db tables: `ingestion_runs`, `ingestion_batches`
- Optional: export `.progress.json` from status for external dashboards
