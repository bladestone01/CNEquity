"""Hybrid sector_bars quality checks: routing vs ingested OHLC sources."""

from __future__ import annotations

from datetime import date

import polars as pl

from stock_data_engine.config import Config
from stock_data_engine.derive.sector_routing import OHLC_TDX
from stock_data_engine.query.parquet_scan import dataset_has_parquet, scan_parquet_root

_SAMPLE = 8


def sector_bars_hybrid_findings(config: Config, trade_date: date) -> list[dict]:
    """Compare routing table expectations to the latest sector_bars snapshot."""
    findings: list[dict] = []
    routing_path = config.meta_root / "sector_ohlc_routing.parquet"
    bars_root = config.curated_root / "sector_bars"

    if not routing_path.exists():
        findings.append(
            {
                "dataset": "sector_bars",
                "severity": "warning",
                "check": "sector_bars_routing_missing",
                "message": (
                    "meta/sector_ohlc_routing.parquet missing; "
                    "run `sde derive sector_routing` before hybrid OHLC"
                ),
            }
        )
        return findings

    if not dataset_has_parquet(bars_root):
        return findings

    routing = pl.read_parquet(routing_path)
    tdx_expected = set(
        routing.filter(pl.col("ohlc_source") == OHLC_TDX)["sector_code"].to_list()
    )

    bars = scan_parquet_root(
        bars_root, partition_col="trade_date", end=trade_date
    ).collect()
    if bars.is_empty():
        return findings

    latest = bars["trade_date"].max()
    snap = bars.filter(pl.col("trade_date") == latest)
    present = set(snap["sector_code"].to_list())

    if "source" not in snap.columns:
        findings.append(
            {
                "dataset": "sector_bars",
                "severity": "warning",
                "check": "sector_bars_source_missing",
                "message": (
                    f"sector_bars on {latest.isoformat()} lacks per-row source; "
                    "re-run hybrid daily/backfill ingestion"
                ),
            }
        )
        return findings

    wrong_source = snap.filter(
        pl.col("sector_code").is_in(list(tdx_expected))
        & (pl.col("source") != OHLC_TDX)
    )
    if not wrong_source.is_empty():
        sample = wrong_source["sector_code"].to_list()[:_SAMPLE]
        findings.append(
            {
                "dataset": "sector_bars",
                "severity": "warning",
                "check": "sector_bars_tdx_routed_em_source",
                "message": (
                    f"{wrong_source.height} TDX-routed board(s) still carry eastmoney OHLC "
                    f"on {latest.isoformat()}"
                ),
                "trade_date": latest.isoformat(),
                "count": wrong_source.height,
                "sample": sample,
            }
        )

    missing_tdx = sorted(tdx_expected - present)
    if missing_tdx:
        findings.append(
            {
                "dataset": "sector_bars",
                "severity": "warning",
                "check": "sector_bars_missing_tdx_routed",
                "message": (
                    f"{len(missing_tdx)} TDX-routed board(s) absent from sector_bars "
                    f"on {latest.isoformat()}"
                ),
                "trade_date": latest.isoformat(),
                "count": len(missing_tdx),
                "sample": missing_tdx[:_SAMPLE],
            }
        )

    mix = snap.group_by("source").len()
    mix_dict = dict(zip(mix["source"].to_list(), mix["len"].to_list(), strict=True))
    findings.append(
        {
            "dataset": "sector_bars",
            "severity": "info",
            "check": "sector_bars_source_mix",
            "message": (
                f"sector_bars {latest.isoformat()}: "
                + ", ".join(f"{k}={v}" for k, v in sorted(mix_dict.items()))
            ),
            "trade_date": latest.isoformat(),
            "source_mix": mix_dict,
            "routing_tdx": len(tdx_expected),
            "routing_em": routing.height - len(tdx_expected),
        }
    )
    return findings
