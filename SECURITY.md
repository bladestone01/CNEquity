# Security Policy

## Supported versions

Security fixes are applied to the latest commit on `main`. Pre-1.0 releases
(`0.x`) do not maintain long-lived patch branches.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Prefer one of:

1. [GitHub Security Advisories](https://github.com/rootSunc/ashare-lake/security/advisories/new)
   (private report), or
2. Open a private channel with the maintainers via the repository owner
   (`rootSunc` on GitHub).

Include:

- Affected version / commit
- Impact (data integrity, credential exposure, remote code execution, etc.)
- Minimal reproduction steps
- Whether a fix or workaround is already known

We aim to acknowledge reports within 7 days and to coordinate disclosure after
a fix is available.

## Scope notes

- This project fetches market data from third-party HTTP/TCP endpoints. Issues
  that are solely upstream site availability, rate limits, or ToS disputes are
  **not** security vulnerabilities — see
  [docs/legal-and-data-sources.md](docs/legal-and-data-sources.md).
- Local config (`configs/ashare-lake.toml`), lake data under `data/`, and runtime
  logs must never be committed. If you discover secrets in git history, report
  privately so history can be scrubbed before wider disclosure.
