"""EastMoney corporate actions (backup for TDX xdxr)."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from stock_data_engine.adapters.eastmoney.datacenter import (
    EastMoneyDatacenterError,
    fetch_datacenter,
)
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.config import Config
from stock_data_engine.domain.symbols import format_symbol

logger = logging.getLogger(__name__)

_REPORT = "RPT_SHAREBONUS_DET"
_COLUMNS = (
    "SECURITY_CODE,SECUCODE,EX_DIV_DATE,CASH_BTAX_RMB,BONUS_RATIO,"
    "TRANSFER_RATIO,ALLOTMENT_RATIO,ALLOTMENT_PRICE,IMPL_PLAN_PROFILE,MARKET_CODE"
)


def _map_action_type(row: dict) -> str | None:
    impl = str(row.get("IMPL_PLAN_PROFILE") or row.get("BONUS_TYPE") or "").lower()
    if "派" in impl or "息" in impl or "现金" in impl:
        return "cash_dividend"
    if "送" in impl:
        return "bonus"
    if "转" in impl:
        return "transfer"
    if "配" in impl:
        return "allotment"
    cash = row.get("CASH_BTAX_RMB") or row.get("CASH_ATAX_RMB")
    if cash and float(cash) > 0:
        return "cash_dividend"
    bonus = row.get("BONUS_RATIO") or row.get("BONUS_IT_RATIO")
    if bonus and float(bonus) > 0:
        return "bonus"
    return None


def _parse_row(row: dict) -> dict | None:
    ex_raw = row.get("EX_DIV_DATE") or row.get("EX_RIGHT_DATE") or row.get("RECORD_DATE")
    if not ex_raw:
        return None
    ex_date = date.fromisoformat(str(ex_raw)[:10])
    code = str(row.get("SECURITY_CODE") or row.get("SECUCODE", "").split(".")[0]).zfill(6)
    market = str(row.get("MARKET_CODE") or row.get("TRADE_MARKET") or "")
    if "SH" in market.upper() or code.startswith(("60", "68")):
        exchange = "SH"
    elif "BJ" in market.upper() or code.startswith("92"):
        exchange = "BJ"
    else:
        exchange = "SZ"
    symbol = format_symbol(code, exchange)
    action_type = _map_action_type(row)
    if not action_type:
        return None

    cash = float(row.get("CASH_BTAX_RMB") or row.get("CASH_ATAX_RMB") or 0)
    bonus = float(row.get("BONUS_RATIO") or row.get("BONUS_IT_RATIO") or 0)
    transfer = float(row.get("TRANSFER_RATIO") or row.get("CONVERSED_RATIO") or 0)
    allot = float(row.get("ALLOTMENT_RATIO") or row.get("IT_RATIO") or 0)
    allot_price = row.get("ALLOTMENT_PRICE") or row.get("IT_PRICE")
    return {
        "symbol": symbol,
        "ex_date": ex_date,
        "action_type": action_type,
        "cash_dividend": cash if action_type == "cash_dividend" else 0.0,
        "bonus_ratio": bonus if action_type == "bonus" else 0.0,
        "transfer_ratio": transfer if action_type == "transfer" else 0.0,
        "allotment_ratio": allot if action_type == "allotment" and allot else None,
        "allotment_price": float(allot_price) if action_type == "allotment" and allot_price else None,
    }


def fetch_corporate_actions_eastmoney(
    trade_date: date,
    *,
    backfill: bool = False,
    client: EastMoneyClient | None = None,
    config: Config | None = None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)

    if backfill:
        date_filter = "(EX_DIV_DATE>='2016-01-01')"
    else:
        ds = trade_date.isoformat()
        date_filter = f"(EX_DIV_DATE='{ds}')"

    retries = config.max_retries if config is not None else 3
    backoff = float(config.retry_backoff_seconds if config is not None else 5)

    try:
        if config is not None:
            config.rate_limit("eastmoney")
        raw = fetch_datacenter(
            client,
            _REPORT,
            _COLUMNS,
            filter_expr=date_filter,
            sort_columns="EX_DIV_DATE",
            sort_types="-1",
            max_retries=retries,
            retry_backoff_seconds=backoff,
        )
    except EastMoneyDatacenterError:
        if owns:
            client.close()
        raise
    except Exception as exc:
        if owns:
            client.close()
        raise EastMoneyDatacenterError(
            f"EastMoney corporate_actions failed for filter {date_filter!r}"
        ) from exc

    rows = []
    for item in raw:
        parsed = _parse_row(item)
        if parsed:
            rows.append(parsed)

    if owns:
        client.close()

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["symbol", "ex_date", "action_type"], keep="last")
