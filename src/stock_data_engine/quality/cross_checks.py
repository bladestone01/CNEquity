"""Cross-dataset reconciliation audit checks.

Single-dataset integrity lives in ``dataset_checks``; these guardrails compare
two curated datasets against each other, catching gaps that neither dataset's
own sentinels can see:

* ``daily_bars`` × ``trading_calendar`` — the whole market must have bars on
  every trading day (a zero-bar trading day is a lost ingestion) and must never
  have bars on a non-trading day (calendar error or forged rows). Per-symbol
  coverage is *not* checked here: suspensions make legitimate per-stock gaps, so
  only market-wide aggregates are meaningful.
* ``valuation_metrics`` × ``daily_bars`` — a valuation row needs a price bar for
  the same symbol/day; large divergence on a shared day is a valuation fetch
  gap. Absolute market-cap sanity is intentionally skipped while baostock leaves
  ``total_mv``/``float_mv`` null.
* ``daily_bars`` × ``adj_factors`` × ``corporate_actions`` — reconciles the
  applied hfq adjustment (``adj_close = close × factor``) against the events that
  should explain it (roadmap G5 / A1 defence line). Two classes, split by whether
  the *adjusted* series stays continuous:
  - a discontinuous adjusted move (adj jumps and diverges from raw) between two
    consecutive trading days is a factor break that poisons every downstream
    factor/backtest → **error** (ports the Workbench guard into the engine and
    refines it: adjacency spares legitimate suspension-resume repricing);
  - a continuous adjusted move whose raw price nonetheless dropped past any board
    limit with no corporate action on record is a real ex-event missing from
    ``corporate_actions`` → **warning** (hfq research is fine; ledger accounting
    is not).
"""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.query.parquet_scan import dataset_has_parquet, scan_parquet_root

# Sample size for date/symbol lists surfaced inside a finding.
_SAMPLE = 8
# A shared trading day where valuation covers less than this fraction of the
# symbols that have bars is flagged as a coverage gap.
_VALUATION_COVERAGE_WARN_RATIO = 0.7

# Adjusted-return discontinuity (the "factor break" class). On two *consecutive*
# trading days a hfq *adjusted* (total-return) move this large that also diverges
# from the raw move by the same margin cannot come from a real corporate action
# (a real ex-event keeps adj≈raw) nor a real price move (board limit ±10–20%): it
# is a factor break that injects an impossible return into every downstream factor
# and backtest. Ports the Workbench guard (``data/quality.py``) into the engine —
# and improves on it: the engine restricts to adjacent trading days, so a
# suspension resume (a large *legitimate* move over a long halt) is not
# false-flagged the way the downstream flat-threshold guard does. Error severity.
ADJ_DISCONTINUITY_RET = 0.35

# Missing corporate action (the lower-severity completeness class). The adjusted
# series is *continuous* (|adj_ret| below the first bound) yet raw and adjusted
# diverge past the second bound — wider than any board price limit, so a real
# ex-event (bonus / large dividend) was adjusted correctly but is absent from
# corporate_actions. hfq research is unaffected (adj is continuous); only ledger
# / dividend accounting is, so this is a warning, not an error.
MISSING_EVENT_MAX_ADJ_RET = 0.15
MISSING_EVENT_MIN_DIVERGENCE = 0.11

# Cap on per-symbol findings per class, so a catastrophic regression floods
# neither the findings file nor health-latest.json. Overflow is summarised.
_MAX_RECON_FINDINGS = 50


def _trading_days(config: Config, trade_date: date) -> set[date]:
    cal_root = config.curated_root / "trading_calendar"
    if not dataset_has_parquet(cal_root):
        return set()
    cal = (
        scan_parquet_root(cal_root, partition_col="trade_date", end=trade_date)
        .filter(pl.col("is_trading"))
        .select("trade_date")
        .unique()
        .collect()
    )
    return set(cal["trade_date"].to_list())


def daily_bars_calendar_findings(config: Config, trade_date: date) -> list[dict]:
    """Reconcile market-wide daily_bars trade dates against the calendar."""
    findings: list[dict] = []
    bars_root = config.curated_root / "daily_bars"
    if not dataset_has_parquet(bars_root):
        return findings
    trading_days = _trading_days(config, trade_date)
    if not trading_days:
        return findings

    bars_dates = set(
        scan_parquet_root(bars_root, partition_col="trade_date", end=trade_date)
        .select("trade_date")
        .unique()
        .collect()["trade_date"]
        .to_list()
    )
    if not bars_dates:
        return findings

    # Bars stamped on a day the calendar says is closed: forged rows or a
    # wrong calendar. Either way downstream date math is corrupted.
    orphan = sorted(bars_dates - trading_days)
    if orphan:
        findings.append(
            {
                "dataset": "daily_bars",
                "severity": "error",
                "check": "daily_bars_calendar_orphan",
                "message": (
                    f"{len(orphan)} trade date(s) have bars but are not calendar "
                    f"trading days (e.g. {', '.join(d.isoformat() for d in orphan[:_SAMPLE])})"
                ),
                "orphan_count": len(orphan),
                "orphan_sample": [d.isoformat() for d in orphan[:_SAMPLE]],
            }
        )

    # Trading days inside the covered span with no bars from any symbol: a whole
    # day of market data lost. (Edges beyond the span are just coverage bounds.)
    first, last = min(bars_dates), max(bars_dates)
    expected = {d for d in trading_days if first <= d <= last}
    missing = sorted(expected - bars_dates)
    if missing:
        findings.append(
            {
                "dataset": "daily_bars",
                "severity": "error",
                "check": "daily_bars_calendar_missing_day",
                "message": (
                    f"{len(missing)} calendar trading day(s) in "
                    f"{first.isoformat()}..{last.isoformat()} have zero bars "
                    f"(e.g. {', '.join(d.isoformat() for d in missing[:_SAMPLE])})"
                ),
                "missing_count": len(missing),
                "missing_sample": [d.isoformat() for d in missing[:_SAMPLE]],
            }
        )
    return findings


def valuation_bars_coverage_findings(config: Config, trade_date: date) -> list[dict]:
    """Reconcile valuation_metrics symbol coverage against daily_bars.

    Two directions:

    * lake-wide — valuation symbols with *no* price bar anywhere are delisted /
      non-tradable names the snapshot should not carry (they never join a
      tradable-universe query downstream, so they are dead weight and a sign the
      valuation step is not filtered to the bar universe);
    * anchor day — of the symbols that traded on the most recent shared day, the
      fraction that valuation actually priced. A low ratio is a fetch gap. This
      is bounded to one day so it does not scan the whole cross product.

    Absolute market-cap sanity is skipped while baostock leaves
    ``total_mv``/``float_mv`` null.
    """
    findings: list[dict] = []
    val_root = config.curated_root / "valuation_metrics"
    bars_root = config.curated_root / "daily_bars"
    if not dataset_has_parquet(val_root) or not dataset_has_parquet(bars_root):
        return findings

    val_syms_all = set(
        scan_parquet_root(val_root, partition_col="trade_date", end=trade_date)
        .select("symbol")
        .unique()
        .collect()["symbol"]
        .to_list()
    )
    bars_syms_all = set(
        scan_parquet_root(bars_root, partition_col="trade_date", end=trade_date)
        .select("symbol")
        .unique()
        .collect()["symbol"]
        .to_list()
    )
    if not val_syms_all or not bars_syms_all:
        return findings

    # Valuation for symbols that never have a bar: delisted / non-tradable names.
    no_bar_ever = sorted(val_syms_all - bars_syms_all)
    if no_bar_ever:
        findings.append(
            {
                "dataset": "valuation_metrics",
                "severity": "warning",
                "check": "valuation_bars_orphan_symbol",
                "message": (
                    f"{len(no_bar_ever)} valuation symbol(s) have no daily_bars row "
                    f"anywhere (delisted/non-tradable; "
                    f"e.g. {', '.join(no_bar_ever[:_SAMPLE])}) — filter the valuation "
                    "step to the bar universe"
                ),
                "orphan_count": len(no_bar_ever),
                "orphan_sample": no_bar_ever[:_SAMPLE],
            }
        )

    # Anchor-day coverage: of symbols that traded, how many did valuation price?
    val_dates = set(
        scan_parquet_root(val_root, partition_col="trade_date", end=trade_date)
        .select("trade_date")
        .unique()
        .collect()["trade_date"]
        .to_list()
    )
    bars_dates = set(
        scan_parquet_root(bars_root, partition_col="trade_date", end=trade_date)
        .select("trade_date")
        .unique()
        .collect()["trade_date"]
        .to_list()
    )
    shared = val_dates & bars_dates
    if not shared:
        findings.append(
            {
                "dataset": "valuation_metrics",
                "severity": "warning",
                "check": "valuation_bars_no_shared_date",
                "message": (
                    "valuation_metrics shares no trade date with daily_bars — "
                    "cannot reconcile symbol coverage"
                ),
            }
        )
        return findings

    anchor = max(shared)
    val_syms = set(
        scan_parquet_root(val_root, partition_col="trade_date", start=anchor, end=anchor)
        .select("symbol")
        .unique()
        .collect()["symbol"]
        .to_list()
    )
    bars_syms = set(
        scan_parquet_root(bars_root, partition_col="trade_date", start=anchor, end=anchor)
        .select("symbol")
        .unique()
        .collect()["symbol"]
        .to_list()
    )
    if not val_syms or not bars_syms:
        return findings

    # Traded symbols missing valuation: a systematic valuation fetch gap.
    covered = val_syms & bars_syms
    ratio = len(covered) / len(bars_syms)
    if ratio < _VALUATION_COVERAGE_WARN_RATIO:
        findings.append(
            {
                "dataset": "valuation_metrics",
                "severity": "warning",
                "check": "valuation_bars_low_coverage",
                "message": (
                    f"valuation covers {len(covered)}/{len(bars_syms)} "
                    f"({ratio:.0%}) of symbols with bars on {anchor.isoformat()} "
                    f"(< {_VALUATION_COVERAGE_WARN_RATIO:.0%})"
                ),
                "anchor_date": anchor.isoformat(),
                "covered_symbols": len(covered),
                "bars_symbols": len(bars_syms),
                "coverage_ratio": round(ratio, 4),
                "warn_ratio": _VALUATION_COVERAGE_WARN_RATIO,
            }
        )
    return findings


def _adjusted_returns(config: Config, trade_date: date) -> pl.DataFrame | None:
    """Per (symbol, day) hfq adjusted vs raw returns, plus the previous bar date.

    ``adj_close = close × factor`` holds exactly for stored hfq factors, so the
    adjusted return is reconstructed by joining ``daily_bars`` (raw close) to the
    derived ``adj_factors`` — no dependency on the query-layer adjustment. Returns
    ``None`` when either dataset is absent (cannot reconcile).
    """
    bars_root = config.curated_root / "daily_bars"
    af_root = config.derived_root / "adj_factors"
    if not dataset_has_parquet(bars_root) or not dataset_has_parquet(af_root):
        return None

    bars = (
        scan_parquet_root(bars_root, partition_col="trade_date", end=trade_date)
        .select("symbol", "trade_date", "close")
        .collect()
    )
    factors = (
        scan_parquet_root(af_root, partition_col="trade_date", end=trade_date)
        .filter(pl.col("adjust_type") == "hfq")
        .select("symbol", "trade_date", "factor")
        .collect()
    )
    if bars.height < 2 or factors.is_empty():
        return None

    joined = bars.join(factors, on=["symbol", "trade_date"], how="inner").filter(
        pl.col("close").is_not_null() & (pl.col("close") > 0) & (pl.col("factor") > 0)
    )
    if joined.height < 2:
        return None

    return (
        joined.with_columns((pl.col("close") * pl.col("factor")).alias("_adj"))
        .sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("raw_ret"),
            (pl.col("_adj") / pl.col("_adj").shift(1).over("symbol") - 1).alias("adj_ret"),
            pl.col("trade_date").shift(1).over("symbol").alias("prev_trade_date"),
        )
        .filter(pl.col("prev_trade_date").is_not_null())
        .with_columns(
            (pl.col("adj_ret") - pl.col("raw_ret")).abs().alias("divergence")
        )
        .select(
            "symbol", "prev_trade_date", "trade_date", "raw_ret", "adj_ret", "divergence"
        )
    )


def _capped_findings(
    ranked: pl.DataFrame, build_one, *, dataset: str, check: str, severity: str, noun: str
) -> list[dict]:
    """Emit one finding per row up to the cap, plus an overflow summary."""
    findings = [build_one(row) for row in ranked.head(_MAX_RECON_FINDINGS).iter_rows(named=True)]
    overflow = ranked.height - _MAX_RECON_FINDINGS
    if overflow > 0:
        findings.append(
            {
                "dataset": dataset,
                "severity": severity,
                "check": f"{check}_overflow",
                "message": (
                    f"{ranked.height} symbols have {noun}; {overflow} beyond the first "
                    f"{_MAX_RECON_FINDINGS} are not listed individually"
                ),
                "total_symbols": ranked.height,
                "listed": _MAX_RECON_FINDINGS,
            }
        )
    return findings


def _worst_per_symbol(df: pl.DataFrame, by: str) -> pl.DataFrame:
    return (
        df.sort(by, descending=True)
        .group_by("symbol", maintain_order=True)
        .first()
    )


def _trading_day_successors(config: Config, trade_date: date) -> pl.DataFrame | None:
    """[prev_trade_date, next_td] — each trading day paired with the next one.

    Used to keep the discontinuity error to *consecutive* trading days. Returns
    ``None`` when the calendar is absent (then adjacency cannot be judged and the
    error stays fail-loud — every discontinuity is reported).
    """
    cal_root = config.curated_root / "trading_calendar"
    if not dataset_has_parquet(cal_root):
        return None
    cal = (
        scan_parquet_root(cal_root, partition_col="trade_date", end=trade_date)
        .filter(pl.col("is_trading"))
        .select("trade_date")
        .unique()
        .collect()
        .sort("trade_date")
    )
    if cal.is_empty():
        return None
    return cal.with_columns(pl.col("trade_date").shift(-1).alias("next_td")).rename(
        {"trade_date": "prev_trade_date"}
    )


def _iso(value) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def adj_factor_reconciliation_findings(config: Config, trade_date: date) -> list[dict]:
    """Reconcile the applied hfq adjustment against corporate_actions (G5).

    Emits an *error* per symbol whose adjusted series is discontinuous between two
    consecutive trading days (a factor break — the Workbench guard, now
    engine-side and refined to spare suspension resumes) and a *warning* per
    symbol with a continuous adjustment whose raw price nonetheless jumped past a
    board limit with no corporate action to explain it (a missing event). Each
    class is ranked worst-first and capped with an overflow summary.
    """
    rets = _adjusted_returns(config, trade_date)
    if rets is None or rets.is_empty():
        return []

    findings: list[dict] = []

    # Error: adjusted-return discontinuity (a factor break). A >35% adjusted move
    # is physically impossible only between *consecutive* trading days (board
    # limit); across a suspension the same threshold false-flags a legitimate
    # resume repricing (e.g. an 8-month restructuring halt), so restrict to
    # adjacent trading days. Corporate actions cannot excuse a consecutive-day
    # discontinuity, so this needs no ex-dates.
    disc = rets.filter(
        (pl.col("adj_ret").abs() > ADJ_DISCONTINUITY_RET)
        & (pl.col("divergence") > ADJ_DISCONTINUITY_RET)
    )
    successors = _trading_day_successors(config, trade_date)
    if successors is not None and not disc.is_empty():
        disc = disc.join(successors, on="prev_trade_date", how="left").filter(
            pl.col("next_td") == pl.col("trade_date")
        )
    breaks = _worst_per_symbol(disc, by="divergence")
    break_syms = set(breaks["symbol"].to_list())
    if not breaks.is_empty():
        findings += _capped_findings(
            breaks.sort("divergence", descending=True),
            lambda row: {
                "dataset": "adj_factors",
                "symbol": row["symbol"],
                "severity": "error",
                "check": "adj_close_discontinuity",
                "message": (
                    f"{row['symbol']}: hfq adjusted return {row['adj_ret']:+.0%} on "
                    f"{_iso(row['trade_date'])} diverges {row['divergence']:.0%} from the "
                    f"raw move ({row['raw_ret']:+.0%}) on consecutive trading days — a "
                    "factor break, not a corporate action"
                ),
                "trade_date": _iso(row["trade_date"]),
                "prev_trade_date": _iso(row["prev_trade_date"]),
                "adj_ret": round(float(row["adj_ret"]), 4),
                "raw_ret": round(float(row["raw_ret"]), 4),
                "divergence": round(float(row["divergence"]), 4),
            },
            dataset="adj_factors",
            check="adj_close_discontinuity",
            severity="error",
            noun="a discontinuous hfq adjustment",
        )

    # Warning: a continuous adjustment absorbing a real ex-event that is missing
    # from corporate_actions. Skip symbols already flagged as breaks.
    ca_root = config.curated_root / "corporate_actions"
    if not dataset_has_parquet(ca_root):
        return findings

    candidates = rets.filter(
        (pl.col("adj_ret").abs() <= MISSING_EVENT_MAX_ADJ_RET)
        & (pl.col("divergence") > MISSING_EVENT_MIN_DIVERGENCE)
        & ~pl.col("symbol").is_in(list(break_syms))
    ).sort(["symbol", "trade_date"])
    if candidates.is_empty():
        return findings

    ex_dates = (
        scan_parquet_root(ca_root, partition_col="ex_date", end=trade_date)
        .select("symbol", "ex_date")
        .unique()
        .collect()
        .sort(["symbol", "ex_date"])
    )
    if ex_dates.is_empty():
        matched = candidates.with_columns(pl.lit(None, dtype=pl.Date).alias("_last_ex"))
    else:
        # A step from bar t_prev to bar t is explained iff a corporate action's
        # ex-date lies in (t_prev, t]; the interval covers suspensions of any
        # length (the adjustment lands on the resume bar). join_asof backward
        # gives the latest ex-date ≤ t; it explains the step when it is > t_prev.
        matched = candidates.join_asof(
            ex_dates.rename({"ex_date": "_last_ex"}),
            left_on="trade_date",
            right_on="_last_ex",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
    missing = _worst_per_symbol(
        matched.filter(
            pl.col("_last_ex").is_null() | (pl.col("_last_ex") <= pl.col("prev_trade_date"))
        ),
        by="divergence",
    )
    if not missing.is_empty():
        findings += _capped_findings(
            missing.sort("divergence", descending=True),
            lambda row: {
                "dataset": "corporate_actions",
                "symbol": row["symbol"],
                "severity": "warning",
                "check": "missing_corporate_action",
                "message": (
                    f"{row['symbol']}: raw return {row['raw_ret']:+.0%} on "
                    f"{_iso(row['trade_date'])} diverges {row['divergence']:.0%} from the "
                    f"hfq adjusted return ({row['adj_ret']:+.0%}) with no corporate action "
                    "on record for that day — an unrecorded ex-event"
                ),
                "trade_date": _iso(row["trade_date"]),
                "prev_trade_date": _iso(row["prev_trade_date"]),
                "adj_ret": round(float(row["adj_ret"]), 4),
                "raw_ret": round(float(row["raw_ret"]), 4),
                "divergence": round(float(row["divergence"]), 4),
            },
            dataset="corporate_actions",
            check="missing_corporate_action",
            severity="warning",
            noun="a raw move with no corporate action on record",
        )
    return findings
