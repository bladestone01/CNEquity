# StockDataEngine

Independent A-share data ingestion, orchestration, and standardization platform.

- **CLI:** `sde`
- **Package:** `stock_data_engine`
- **Delivery:** Curated Parquet lake + optional DuckDB views

## Quick start

```bash
pip install -e ".[tdx,dev]"
sde init --config configs/stockdata.example.toml
sde config validate --config configs/stockdata.example.toml
sde run daily --config configs/stockdata.example.toml
sde status --config configs/stockdata.example.toml
```

## Documentation

- [PRD](docs/PRD.md)
- [Schema contract](docs/schema.md)
- [Dataset catalog](docs/datasets.md)
- [Operations guide](docs/operations.md)

## License

MIT
