# StockDataEngine

Independent A-share data ingestion, orchestration, and standardization platform.

- **CLI:** `sde`
- **Package:** `stock_data_engine`
- **Delivery:** Curated Parquet lake + optional DuckDB views

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[tdx,dev]"
```

## Quick start

```bash
sde init --config configs/stockdata.example.toml
sde config validate --config configs/stockdata.example.toml
sde run daily --config configs/stockdata.example.toml
sde status --config configs/stockdata.example.toml
```

## Documentation

- [PRD v2.0](docs/PRD.md)
- [Schema contract](docs/schema.md)
- [Dataset catalog](docs/datasets.md)
- [Operations guide](docs/operations.md)
- [Architecture decisions (ADR)](docs/adr/)
- [Contributing](CONTRIBUTING.md)

## License

MIT
