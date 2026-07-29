"""Shenwan (申万) industry classification history — C2 backfill source.

Source: SwClass2021 ``StockClassifyUse_stock.xls`` (interval rows with 计入日期).
Daily EastMoney industry snapshots stay ``classification_system=eastmoney``;
historical rows use ``classification_system=sw``.
"""

from __future__ import annotations

import io
import logging
from datetime import date

import httpx
import polars as pl

from ashare_lake.domain.symbols import format_symbol, is_all_a_symbol

logger = logging.getLogger(__name__)

__all__ = [
    "SW_INDUSTRY_XLS_URL",
    "exchange_from_code",
    "fetch_sw_industry_intervals",
    "expand_sw_industry_as_of",
]

SW_INDUSTRY_XLS_URL = (
    "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ashare-lake/0.1)"}


def exchange_from_code(code: str) -> str:
    if code.startswith(("60", "68")):
        return "SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "BJ"
    return "SZ"


def _code_to_symbol(code: str) -> str | None:
    code = str(code).zfill(6)
    exchange = exchange_from_code(code)
    if not is_all_a_symbol(code, exchange):
        return None
    return format_symbol(code, exchange)


def fetch_sw_industry_intervals(*, client: httpx.Client | None = None) -> pl.DataFrame:
    """Download Shenwan stock→industry interval history (one row per classification spell)."""
    try:
        import pandas as pd  # noqa: PLC0415 — optional; Excel parse only
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pandas is required to parse Shenwan industry XLS; "
            "reinstall with `pip install --force-reinstall ashare-lake` "
            "(or `pip install -e .` from a source checkout)."
        ) from exc

    owns = client is None
    if client is None:
        client = httpx.Client(timeout=120.0, follow_redirects=True)
    try:
        resp = client.get(SW_INDUSTRY_XLS_URL, headers=_HEADERS)
        resp.raise_for_status()
    finally:
        if owns:
            client.close()

    pdf = pd.read_excel(
        io.BytesIO(resp.content),
        dtype={"股票代码": str, "行业代码": str},
    )
    if pdf.empty:
        raise RuntimeError("Shenwan industry XLS returned no rows")

    pdf = pdf.rename(
        columns={
            "股票代码": "code",
            "计入日期": "start_date",
            "行业代码": "industry_code",
            "更新日期": "update_time",
        }
    )
    pdf["start_date"] = pd.to_datetime(pdf["start_date"], errors="coerce").dt.date
    pdf = pdf.dropna(subset=["code", "start_date", "industry_code"])
    rows: list[dict] = []
    for rec in pdf.to_dict(orient="records"):
        sym = _code_to_symbol(str(rec["code"]))
        if not sym:
            continue
        rows.append(
            {
                "symbol": sym,
                "start_date": rec["start_date"],
                "industry_code": str(rec["industry_code"]).strip(),
            }
        )
    if not rows:
        raise RuntimeError("Shenwan industry XLS produced no all_a symbols")
    return pl.DataFrame(rows).sort(["symbol", "start_date"])


def expand_sw_industry_as_of(
    intervals: pl.DataFrame,
    as_of_dates: list[date],
) -> pl.DataFrame:
    """Point-in-time expand: for each as_of, latest start_date <= as_of per symbol."""
    if not as_of_dates:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "classification_system": pl.Utf8,
                "industry_code": pl.Utf8,
                "industry_name": pl.Utf8,
                "as_of_date": pl.Date,
            }
        )
    frames: list[pl.DataFrame] = []
    for as_of in as_of_dates:
        snap = (
            intervals.filter(pl.col("start_date") <= as_of)
            .sort(["symbol", "start_date"])
            .unique(subset=["symbol"], keep="last")
            .select(
                pl.col("symbol"),
                pl.lit("sw").alias("classification_system"),
                pl.col("industry_code"),
                # Official name map is a separate SW publish; code is stable for joins.
                pl.col("industry_code").alias("industry_name"),
                pl.lit(as_of).alias("as_of_date"),
            )
        )
        if not snap.is_empty():
            frames.append(snap)
    if not frames:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "classification_system": pl.Utf8,
                "industry_code": pl.Utf8,
                "industry_name": pl.Utf8,
                "as_of_date": pl.Date,
            }
        )
    return pl.concat(frames)
