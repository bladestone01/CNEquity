# Contributing

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx,dev]"
```

## Common tasks

```bash
ruff format .          # format
ruff check .           # lint
pytest                 # all tests
pytest tests/unit      # fast unit tests only
pytest tests/integration
```

## Conventions

- **Package layout:** all code under `src/stock_data_engine/`; one concern per
  subpackage (`adapters`, `orchestrator`, `steps`, `storage`, `domain`,
  `derive`, `quality`, `duckdb`, `catalog`, `cli`).
- **Data contract first:** new datasets must declare schema + primary key in
  `domain/schemas.py` and a partition key, and carry provenance columns
  (`source`, `data_version`, `fetched_at`).
- **Adapters are thin:** I/O and source quirks live in `adapters/`; business
  logic and normalization live in `steps/` / `domain/`.
- **Tests:** unit tests must run offline (mock/monkeypatch network). Network
  access belongs only in clearly marked integration tests.
- **Decisions:** record non-trivial architecture choices as an ADR under
  `docs/adr/` (copy `0000-template.md`).

## Definition of done for a new dataset/step

1. Schema + PK + partition key declared.
2. `@register_step` with correct `depends_on` / `group` / `requires_workers`.
3. Write-time schema validation passes.
4. Unit test covering normalization + at least one edge case.
5. Entry added to `docs/datasets.md`.
