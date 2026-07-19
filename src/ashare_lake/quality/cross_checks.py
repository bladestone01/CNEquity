"""Cross-dataset reconciliation checks.

Single-dataset integrity is in ``dataset_checks``. Here:

* ``daily_bars`` × ``trading_calendar`` — market-wide only (per-symbol gaps are
  often suspensions).
* ``valuation_metrics`` × ``daily_bars`` — coverage on shared days; skip absolute
  mcap sanity while baostock leaves ``total_mv``/``float_mv`` null.
* ``daily_bars`` × ``adj_factors`` × ``corporate_actions`` — hfq continuity vs
  recorded ex-events. Consecutive trading days only (spares suspension resumes).
"""

from __future__ import annotations

from datetime import date

import polars as pl

from ashare_lake.config import Config
from ashare_lake.query.parquet_scan import dataset_has_parquet, scan_parquet_root

_SAMPLE = 8
# Flag when valuation covers less than this share of symbols with bars that day.
_VALUATION_COVERAGE_WARN_RATIO = 0.7

# Error: |adj_ret| and |adj_ret - raw_ret| both above this on consecutive TDs
# (beyond board limits; not a real ex-event).
ADJ_DISCONTINUITY_RET = 0.35

# Warning: adj continuous but raw diverges past board limit with no CA on record.
MISSING_EVENT_MAX_ADJ_RET = 0.15
MISSING_EVENT_MIN_DIVERGENCE = 0.11

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

    # Bars on a closed calendar day.
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

    # Trading days in the covered span with zero bars from any symbol.
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
    """valuation_metrics vs daily_bars: orphan symbols + one-day coverage ratio."""
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
    """Per (symbol, day) hfq adj vs raw returns + previous bar date. None if missing data."""
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
        .with_columns((pl.col("adj_ret") - pl.col("raw_ret")).abs().alias("divergence"))
        .select("symbol", "prev_trade_date", "trade_date", "raw_ret", "adj_ret", "divergence")
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
    return df.sort(by, descending=True).group_by("symbol", maintain_order=True).first()


def _trading_day_successors(config: Config, trade_date: date) -> pl.DataFrame | None:
    """[prev_trade_date, next_td]. None if calendar missing (then no adjacency filter)."""
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
    """hfq continuity vs corporate_actions; errors/warnings capped per class."""
    rets = _adjusted_returns(config, trade_date)
    if rets is None or rets.is_empty():
        return []

    findings: list[dict] = []

    # Factor break on consecutive TDs only (suspension resumes false-flag otherwise).
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

    # Continuous adj but raw jumped with no CA; skip symbols already flagged.
    ca_root = config.curated_root / "corporate_actions"
    if not dataset_has_parquet(ca_root):
        return findings

    candidates = rets.filter(
        (pl.col("adj_ret").abs() <= MISSING_EVENT_MAX_ADJ_RET)
        & (pl.col("divergence") > MISSING_EVENT_MIN_DIVERGENCE)
        & ~pl.col("symbol").is_in(list(break_syms))
    ).sort(["symbol", "trade_date"])
    if successors is not None and not candidates.is_empty():
        candidates = candidates.join(successors, on="prev_trade_date", how="left").filter(
            pl.col("next_td") == pl.col("trade_date")
        )
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
        # Explained if some ex-date is in (t_prev, t].
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
