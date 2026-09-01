# Release and data-contract governance

CNEquity ships two related public interfaces: the Python/CLI package and the
datasets stored in a user's lake. A release is ready only when both interfaces
have an explicit compatibility decision.

## Version policy

- Package versions follow Semantic Versioning. During `0.x`, a minor release
  may contain a planned breaking change; the changelog must identify it.
- Every dataset has an independent `schema_version`, contract fingerprint and
  compatibility policy. Package version alone is never a data-revision id.
- Additive nullable columns are compatible under the current `additive`
  policy. Removing a column, changing its type/unit/primary key, weakening PIT
  semantics, or changing history meaning requires a schema-version increase
  and a migration note.
- A deprecated Python/CLI spelling remains available for at least one minor
  release unless retaining it would make data incorrect or unsafe. Deprecation
  warnings must name the replacement and the planned removal boundary.

## Required pull-request evidence

Changes to a dataset or source must include all applicable items:

1. machine-readable contract diff and consumer-contract test;
2. offline adapter fixture or parser boundary test;
3. primary/backup source and blast-radius update;
4. terms/redistribution review in `sources/SOURCES.yml`;
5. migration, rollback and PIT-quality notes;
6. unit tests on Linux, Windows and macOS; formatting and lint checks;
7. dependency audit and CycloneDX SBOM artifact.

Unknown source permissions, unknown historical availability and unknown PIT
timestamps fail closed. A source being technically reachable is not evidence
that redistribution or commercial use is allowed.

## Release gate

Before tagging:

- `cne contract validate` succeeds and the diff against the last release is
  reviewed;
- all offline tests and wheel smoke tests pass;
- core dataset failures are absent; research/advisory failures are visible as
  `degraded`, never silently converted to success;
- snapshot create/verify/restore has been exercised into an empty target;
- the current stability report contains the required consecutive trading-day
  evidence for a production-readiness claim;
- source SLO and legal-policy reports are attached to the release record.

Release artifacts are built once in GitHub Actions and published with trusted
publishing. Do not rebuild a wheel locally for the same tag.

## Incident ownership

The default CODEOWNER triages contract breaks, source incidents and security
reports. Source regressions use the structured source-regression issue form;
security vulnerabilities use the private process in `SECURITY.md`. A repeated
source-health failure produces a deterministic incident payload so reruns
update one incident rather than creating duplicates.
