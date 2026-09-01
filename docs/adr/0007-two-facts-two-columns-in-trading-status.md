# ADR 0007: Two facts, two columns, in trading_status

- Status: Accepted
- Date: 2026-08-30
- Relates to: [ADR-0006](0006-publishers-over-vendors.md)

## Context

`trading_status.status` carried two independent facts in one string: whether a
security was **trading** that day, and whether it was under the exchange's
**risk-warning** designation (ST / *ST). A security can be both at once, and a
single column cannot say so. The writer resolved the conflict with an
`if/elif`, and halting won:

```
000711.SZ (ST京蓝)   2026-08-27  status=st
                     2026-08-28  status=suspended
```

The company did not leave risk warning that day — it halted. The designation
was dropped from the stored history, and `derive/market_breadth.py`, which
reads it to choose the ±5% limit band rather than ±10%, measured that session
against the wrong band.

The same `if/elif` had an `else` that called everything remaining `normal` with
`is_trading=True`, with no concept of delisting. Measured on a full lake for
2026-08-28: **611 symbols carrying a `delist_date` — one of them since
1999-07-12 — were published as normally trading**, none of them with a bar that
day. A vendor board cannot report on a security that has left the market, so no
snapshot source could ever have fixed this.

Both defects surfaced together through `st_labels_vs_exchange`, which reported
four symbols the exchanges designate ST that curated did not label. Two were
halted ST names (the first defect) and two were delisted names the exchange
still lists pending deregistration (the second). The check's own module comment
had anticipated that second pair and asserted the shared-universe restriction
handled them — it did not, precisely because the delisting defect kept issuing
them "normal" rows and so kept them inside the shared universe.

## Decision

1. **`status` is the trading state alone**: `normal` | `suspended` |
   `delisted`. **`risk_warning` is a separate nullable boolean** carrying the
   ST / *ST designation. Writers set them independently; no precedence rule
   exists because there is no longer a conflict to resolve.

2. **Delisted rows are written from `instruments`, not requested from a
   vendor.** A delisted symbol is removed from the daily board request and gets
   `status=delisted`, `is_trading=False`, `source=derived_delisted`. Its
   `risk_warning` comes from the final 简称 (`*ST元成`), the only ST evidence
   left once the boards drop the symbol. Scoping is per session, so a watermark
   catch-up still treats the sessions before delisting as live.

3. **`risk_warning` is nullable and null means "no evidence".** A derived
   bar-gap suspension row proves the security did not trade; it proves nothing
   about risk warning, and writing `False` there would invent a fact.
   `quality/st_coverage.py` already owns the question of where ST evidence is
   missing.

4. **Reads work on both encodings.** `validate_dataframe` upgrades legacy
   frames (`status="st"`) on the way in, for every read path at once, so a lake
   is correct before and after migration.
   `scripts/migrate_trading_status_risk_warning.py` makes the physical schema
   uniform.

## Consequences

- The question "was this security trading on day X" is now answerable from this
  dataset alone, in all three states.
- `market_breadth` prices halted ST names against the correct band.
- The tradable-universe filter tests risk warning as its own predicate, so a
  name cannot slip in by being halted-and-ST.
- **ST and *ST are not distinguished.** No source feeding this dataset ever
  made the distinction — Baostock exposes one `isST` flag and the Tushare
  adapter already collapsed its `ST`/`*ST` type — so the boolean is what the
  evidence supports. The finer designation stays in the exchange 简称, via
  `instruments.name` and `adapters.exchange.st_lists.is_st_name`.
- The migration does **not** back-fill `delisted` rows into history, and it
  cannot recover an ST label that a suspended row already destroyed: neither is
  present in the stored bytes. Correcting a past window means re-running the
  daily step over it.
- The legacy-upgrade branch in `validate_dataframe` is deliberate debt with a
  stated exit: delete it once every lake has run the migration.

## Alternatives considered

- **Keep one column, add composite values** (`st_suspended`, …). Smallest
  change, but the enum multiplies with every new dimension and every consumer
  has to know the product rather than the factors. It also leaves the same
  question — which fact wins — merely renamed.
- **Reorder the if/elif so ST wins over suspension.** Trades one silent loss
  for the mirror-image one: halts would vanish for ST names instead.
- **Drop delisted symbols from `trading_status` entirely.** Cheaper, and
  "absent" would mean "not in the market". But absence is indistinguishable
  from a coverage gap, and this dataset's whole job is to answer the trading
  question explicitly.
- **Back-fill `delisted` rows across history in the migration.** Would make old
  partitions look tidy while stamping today's delist dates onto sessions that
  never observed them — a manufactured point-in-time fact, which is the exact
  failure the PIT work exists to prevent.
