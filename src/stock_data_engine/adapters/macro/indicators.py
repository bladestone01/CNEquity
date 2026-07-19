"""Macro indicators — daily bond/SHIBOR via EastMoney; monthly via optional akshare."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.datacenter import (
    EastMoneyDatacenterError,
    fetch_datacenter,
)
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_TREASURY_REPORT = "RPTA_WEB_TREASURYYIELD"
_TREASURY_COLUMNS = "SOLAR_DATE,EMM00166466"
_SHIBOR_REPORT = "RPT_IMP_INTRESTRATEN"
_SHIBOR_COLUMNS = "REPORT_DATE,IR_RATE"
_SHIBOR_FILTER = '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="203")'
_LPR_REPORT = "RPTA_WEB_RATE"
_LPR_COLUMNS = "TRADE_DATE,LPR1Y"

_AKSHARE_SERIES = {
    "lpr_1y": ("macro_china_lpr", "LPR1Y", "monthly"),
    "pmi_manufacturing": ("macro_china_pmi", "制造业", "monthly"),
    "m2_yoy": ("macro_china_money_supply", "M2-同比增长", "monthly"),
    "social_financing": ("macro_china_shrzgm", "社会融资规模增量", "monthly"),
}


def _parse_obs_date(value: object, fallback: date) -> date:
    if value is None:
        return fallback
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return fallback


_MONTH_RE = re.compile(r"^(\d{4})[-年/.](\d{1,2})月?")


def _parse_series_obs_date(value: object) -> date | None:
    """Parse an akshare observation date; monthly values map to month end.

    Returns None when unparseable — the row is dropped rather than stamped
    with a fabricated date.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = _MONTH_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, last_day)
    return None


def _eastmoney_daily(client: EastMoneyClient, trade_date: date) -> list[dict]:
    ds = trade_date.isoformat()
    rows: list[dict] = []

    try:
        treasury = fetch_datacenter(
            client,
            _TREASURY_REPORT,
            _TREASURY_COLUMNS,
            filter_expr=f"(SOLAR_DATE='{ds}')",
        )
    except EastMoneyDatacenterError as exc:
        logger.warning("EastMoney treasury indicator fetch skipped: %s", exc)
        treasury = []
    for item in treasury:
        val = item.get("EMM00166466")
        if val is not None:
            rows.append(
                {
                    "indicator_id": "cnbond_yield_10y",
                    "obs_date": _parse_obs_date(item.get("SOLAR_DATE"), trade_date),
                    "value": float(val),
                    "frequency": "daily",
                }
            )

    try:
        shibor = fetch_datacenter(
            client,
            _SHIBOR_REPORT,
            _SHIBOR_COLUMNS,
            filter_expr=f"{_SHIBOR_FILTER}(REPORT_DATE='{ds}')",
        )
    except EastMoneyDatacenterError as exc:
        logger.warning("EastMoney SHIBOR indicator fetch skipped: %s", exc)
        shibor = []
    for item in shibor:
        val = item.get("IR_RATE")
        if val is not None:
            rows.append(
                {
                    "indicator_id": "shibor_3m",
                    "obs_date": _parse_obs_date(item.get("REPORT_DATE"), trade_date),
                    "value": float(val),
                    "frequency": "daily",
                }
            )

    try:
        lpr = fetch_datacenter(
            client,
            _LPR_REPORT,
            _LPR_COLUMNS,
            filter_expr=f"(TRADE_DATE='{ds}')",
        )
    except EastMoneyDatacenterError as exc:
        logger.warning("EastMoney LPR indicator fetch skipped: %s", exc)
        lpr = []
    for item in lpr:
        val = item.get("LPR1Y")
        if val is not None:
            rows.append(
                {
                    "indicator_id": "lpr_1y",
                    "obs_date": _parse_obs_date(item.get("TRADE_DATE"), trade_date),
                    "value": float(val),
                    "frequency": "monthly",
                }
            )

    return rows


def _akshare_rows(trade_date: date, *, config=None) -> list[dict]:
    try:
        import akshare as ak  # type: ignore[import-not-found]
    except ImportError:
        return []

    rows: list[dict] = []
    for indicator_id, (func_name, col_hint, frequency) in _AKSHARE_SERIES.items():
        if indicator_id == "lpr_1y":
            continue  # prefer EastMoney LPR when present
        if config is not None:
            config.rate_limit("akshare")
        try:
            func = getattr(ak, func_name)
            pdf = func()
        except Exception as exc:
            logger.debug("akshare %s failed: %s", func_name, exc)
            continue
        if pdf is None or pdf.empty:
            continue

        date_col = pdf.columns[0]
        value_col = next((c for c in pdf.columns if col_hint in str(c)), pdf.columns[-1])
        # akshare returns the full published series. Keep everything up to
        # trade_date — monthly obs dates almost never equal the run day, and
        # compact dedupes by (indicator_id, obs_date) so re-ingesting is
        # idempotent. Filtering to obs == trade_date would drop ~all rows.
        for _, rec in pdf.iterrows():
            obs = _parse_series_obs_date(rec.get(date_col))
            if obs is None or obs > trade_date:
                continue
            val = rec.get(value_col)
            if val is None or (isinstance(val, float) and val != val):
                continue
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "obs_date": obs,
                    "value": float(val),
                    "frequency": frequency,
                }
            )
    return rows


def fetch_macro_indicators(
    trade_date: date,
    *,
    client: EastMoneyClient | None = None,
    config=None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)

    rows = _eastmoney_daily(client, trade_date)
    if owns:
        client.close()

    seen = {(r["indicator_id"], r["obs_date"]) for r in rows}
    for item in _akshare_rows(trade_date, config=config):
        key = (item["indicator_id"], item["obs_date"])
        if key not in seen:
            rows.append(item)
            seen.add(key)

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["indicator_id", "obs_date"], keep="last")
