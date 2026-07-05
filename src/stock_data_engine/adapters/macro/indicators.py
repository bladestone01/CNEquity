"""Macro indicators — daily bond/SHIBOR via EastMoney; monthly via optional akshare."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.datacenter import fetch_datacenter
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)

_TREASURY_REPORT = "RPTA_WEB_TREASURY_YIELD"
_TREASURY_COLUMNS = "TRADE_DATE,TENYEAR"
_SHIBOR_REPORT = "RPTA_WEB_SHIBOR"
_SHIBOR_COLUMNS = "TRADE_DATE,SHIBOR_3M"
_LPR_REPORT = "RPTA_WEB_LPR"
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


def _eastmoney_daily(client: EastMoneyClient, trade_date: date) -> list[dict]:
    ds = trade_date.isoformat()
    rows: list[dict] = []

    treasury = fetch_datacenter(
        client,
        _TREASURY_REPORT,
        _TREASURY_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
    )
    for item in treasury:
        val = item.get("TENYEAR")
        if val is not None:
            rows.append(
                {
                    "indicator_id": "cnbond_yield_10y",
                    "obs_date": _parse_obs_date(item.get("TRADE_DATE"), trade_date),
                    "value": float(val),
                    "frequency": "daily",
                }
            )

    shibor = fetch_datacenter(
        client,
        _SHIBOR_REPORT,
        _SHIBOR_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
    )
    for item in shibor:
        val = item.get("SHIBOR_3M")
        if val is not None:
            rows.append(
                {
                    "indicator_id": "shibor_3m",
                    "obs_date": _parse_obs_date(item.get("TRADE_DATE"), trade_date),
                    "value": float(val),
                    "frequency": "daily",
                }
            )

    lpr = fetch_datacenter(
        client,
        _LPR_REPORT,
        _LPR_COLUMNS,
        filter_expr=f"(TRADE_DATE='{ds}')",
    )
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


def _akshare_rows(trade_date: date) -> list[dict]:
    try:
        import akshare as ak  # type: ignore[import-not-found]
    except ImportError:
        return []

    rows: list[dict] = []
    for indicator_id, (func_name, col_hint, frequency) in _AKSHARE_SERIES.items():
        if indicator_id == "lpr_1y":
            continue  # prefer EastMoney LPR when present
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
        for _, rec in pdf.iterrows():
            obs = _parse_obs_date(rec.get(date_col), trade_date)
            if obs != trade_date:
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
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient()

    rows = _eastmoney_daily(client, trade_date)
    if owns:
        client.close()

    seen = {(r["indicator_id"], r["obs_date"]) for r in rows}
    for item in _akshare_rows(trade_date):
        key = (item["indicator_id"], item["obs_date"])
        if key not in seen:
            rows.append(item)
            seen.add(key)

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["indicator_id", "obs_date"], keep="last")
