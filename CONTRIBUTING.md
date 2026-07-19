# Contributing

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security issues go through
[SECURITY.md](SECURITY.md), not public issues.

Before proposing large features, check [docs/comparison.md](docs/comparison.md)
(scope: data layer only) and [docs/legal-and-data-sources.md](docs/legal-and-data-sources.md).

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx,dev]"
# optional: valuation, macro, nlp, structure — see docs/getting-started/installation.md
```

Do not commit `configs/stockdata.toml`, `data/`, or `logs/`.

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
  subpackage (`domain` contracts, `adapters` source I/O, `orchestrator`
  engine/manifest/worker pool, `steps` one module per data layer, `storage`
  lake writes + layout, `derive`, `quality`, `query` views/on-demand/read API,
  `config`, `cli`).
- **Steps by data layer:** step modules under `steps/` follow L0–L8
  layering (`reference.py` = L0, `bars.py` = L1, `events.py` = L2, …,
  `finalize.py`); a new dataset's step goes into its layer module (create it
  if missing) and must be imported in `steps/__init__.py` to register.
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
5. Entry added to [`docs/datasets/catalog.md`](docs/datasets/catalog.md) and [`docs/datasets/sources.md`](docs/datasets/sources.md).
