# Contributing

Security issues go through [SECURITY.md](SECURITY.md), not public issues.

Before proposing large features, check [docs/comparison.md](docs/comparison.md)
(scope: data layer only) and [docs/legal-and-data-sources.md](docs/legal-and-data-sources.md).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[tdx,dev]"
# optional: valuation, macro, nlp, structure — see docs/getting-started/installation.md
```

Do not commit `configs/stockdata.toml`, `data/`, or `logs/`.

```bash
ruff format .
ruff check .
pytest                 # all
pytest tests/unit      # fast
pytest tests/integration
```

## Conventions

- Code lives under `src/stock_data_engine/`; keep concerns split (`domain`,
  `adapters`, `orchestrator`, `steps`, `storage`, `derive`, `quality`,
  `query`, `config`, `cli`).
- Steps follow L0–L8 layering under `steps/`; import new modules in
  `steps/__init__.py` so they register.
- New datasets need schema + PK in `domain/schemas.py`, a partition key, and
  provenance columns (`source`, `data_version`, `fetched_at`).
- Adapters stay thin (I/O + source quirks); normalization belongs in
  `steps/` / `domain/`.
- Unit tests stay offline. Network only in clearly marked integration tests.
- Non-trivial architecture choices go in `docs/adr/` (copy `0000-template.md`).

## New dataset checklist

1. Schema + PK + partition key
2. `@register_step` with `depends_on` / `group` / `requires_workers`
3. Write-time schema validation passes
4. Unit test for normalization + at least one edge case
5. Entries in [`docs/datasets/catalog.md`](docs/datasets/catalog.md) and
   [`docs/datasets/sources.md`](docs/datasets/sources.md)
