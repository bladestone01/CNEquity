# ADR 0003: Canonical curated rows with separate multi-source snapshots

- Status: Accepted
- Date: 2026-06-28

## Context

The same field (e.g. a close price or dividend) can be served by multiple
sources (tdx_protocol, eastmoney, sina, ...) with subtle discrepancies. If
backup sources silently overwrite primary data, lineage and reproducibility
break.

## Decision

`curated/` holds exactly **one canonical row per primary key**, with `source`,
`data_version`, `fetched_at` as **columns** (not partition keys). Backup-source
data lands in `meta/source_snapshots/{dataset}/source=.../data_version=.../`.
The `audit` step compares primary vs snapshots and emits `source_diffs`.
**Source switching is never automatic** — a human decides.

## Consequences

- Reproducible, auditable canonical dataset; provenance always present.
- Cross-source disagreements are observable instead of hidden.
- Requires snapshot storage + diff tooling (roadmap M4).

## Alternatives considered

- Last-writer-wins across sources: hides disagreements, non-reproducible.
- Storing every source in curated with a `source` partition: explodes
  partitions and pushes conflict resolution onto every consumer.
