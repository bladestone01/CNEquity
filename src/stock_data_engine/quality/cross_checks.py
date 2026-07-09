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
