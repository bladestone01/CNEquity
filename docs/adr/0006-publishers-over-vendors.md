# ADR 0006: Publishers over vendors, where a publisher exists

- Status: Accepted
- Date: 2026-08-30
- Relates to: [ADR-0003](0003-canonical-curated-with-source-snapshots.md),
  [ADR-0005](0005-source-routing-vs-switching.md)

## Context

Two thirds of the lake's datasets have EastMoney as their primary source, and
only `daily_bars` and `corporate_actions` have any failover configured at all.
`economic_calendar` already shows what that concentration costs: EastMoney
retired `RPT_ECONOMICCALENDAR` and the dataset is now a schema placeholder with
no rows.

Concentration is the visible half of the problem. The other half is that
every price arbiter we had compares one redistributor against another.
`daily_bars` is TDX against EastMoney; when they agree, that establishes they do
not differ, not that either is right. Neither of them computed the number.

For some of what the lake carries, a publisher exists and is reachable:

- The exchanges publish their own closing quotes.
- The exchanges compile 融资融券 from member-firm reports and publish the
  per-security detail themselves — EastMoney can only copy that file.

`quality/authority_checks.py` already established the pattern for the first
case (PMI against the NBS release, ST designations against the exchange
listings). It had no equivalent for prices.

## Decision

Where a publisher exists and is reachable, prefer it — but in the weakest form
that fits the data.

1. **Arbitration, not ownership, for `daily_bars`.** TDX stays primary. A new
   `daily_bars_vs_exchange` authority check compares curated against the closes
   the SSE and SZSE publish. It is advisory: it reports, it never gates a
   revision and never writes a row.

2. **Ownership for `margin_trading`.** `[margin_trading] source` selects the
   publisher, defaulting to `exchange`. EastMoney stays selectable, and the
   choice is an operator's — never an automatic fallback, per ADR-0003.

3. **An independent derivation counts as a second source.** `adj_factors` has
   one vendor and no second feed to compare against, but the factor step on an
   ex-date is fully determined by the corporate action and the prior close.
   Recomputing it from curated `corporate_actions` — a *different* vendor —
   makes the single-source table checkable without adding a source.

Measurement decided each of these rather than preference. Against the
publishers on 2026-08-28/26:

- `daily_bars` OHLC matched **exactly**, every field, all 5,212 shared symbols.
  So TDX needs no replacing; what it needed was proof, which it now has.
- `daily_bars` turnover did **not**, in one direction only: 305 SZ symbols
  carried less curated volume than the exchange published, never more. The
  exchange daily total folds in trading a continuous-auction bar excludes, so
  this is definitional. It is summarised once and judged on the *share* of the
  universe that diverges, not per symbol.
- `margin_trading` matched EastMoney exactly on all four fields over 3,522
  shared symbols, while the exchanges carried 4,100 securities to EastMoney's
  3,857. Same numbers, more coverage, one less hop.

## Consequences

- The lake gains its first price check against a body that publishes prices.
  "Both vendors wrong the same way" is now detectable for `daily_bars`.
- `adj_factors` stops being unverifiable. The continuity tripwire only caught
  gross corruption (>20x); the recomputation catches a step of the wrong size
  or on the wrong day, which is the failure that actually occurs.
- `margin_trading` costs about one session of freshness. SZSE publishes a
  business day after SSE, and a day is written only once both have — a
  half-market day would advance the watermark and strand the other half.
- **SSE does not publish 融券余额.** `short_balance` is null on SH rows under
  the exchange source. It is reconstructible as 融券余量 × close, but stamping
  local arithmetic with `source="exchange"` would attribute it to the exchange,
  so the gap is carried instead. Operators who need the field select
  `source = "eastmoney"`.
- Publisher horizons differ and are now part of the contract: SSE's quote
  endpoint serves only the session it is currently publishing, SZSE's report
  serves any past date. Every result names which exchanges answered, so a
  SZSE-only comparison can never read as covering the market.
- The three new comparisons are advisory. None fails a run; they mark it
  degraded and land in the ordinary findings stream.

## Alternatives considered

- **Promote the exchange to `daily_bars` primary.** The measurement argues
  against it: TDX already matches exactly, serves the whole market including
  Beijing in one protocol session, and has no publication lag. Replacing it
  would trade a working primary for a slower one to fix nothing.
- **Fill SH `short_balance` from 融券余量 × close.** Correct arithmetic, wrong
  provenance. A locally computed number carrying `source="exchange"` is the
  kind of quiet dishonesty `SOURCES.yml` exists to prevent.
- **Write `margin_trading` per exchange as each publishes.** This is genuine
  routing under ADR-0005 — SH and SZ keys are disjoint — but the incremental
  watermark is per date, so a half-written day would never be completed. Doing
  it properly needs per-exchange coverage state, which is not worth one session
  of latency.
- **Gate revisions on the exchange comparison.** Tempting for prices, but the
  turnover gap is definitional and SSE cannot serve historical sessions. A gate
  that cannot run on a backfill, and that fires on a known-benign difference,
  would be turned off within a week.
- **Move `dragon_tiger` and `block_trades` too.** Intended, and not done: no
  stable official endpoint was found for either within this change (see the
  note in `docs/datasets/catalog.md`). Shipping a guessed parser would replace
  a working source with a broken one.
