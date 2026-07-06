"""Shared EastMoney response helpers."""

from __future__ import annotations

from stock_data_engine.domain.symbols import format_symbol, is_all_a_symbol

ALL_A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
DATACENTER_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
PUSH2_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"


def symbol_from_secucode(secucode: str | None) -> str | None:
    """Parse ``600519.SH`` / ``000001.SZ`` style codes from datacenter rows."""
    if not secucode:
        return None
    text = str(secucode).strip().upper()
    if "." not in text:
        return None
    code, exchange = text.split(".", 1)
    code = code.zfill(6)
    if exchange not in {"SH", "SZ", "BJ"}:
        return None
    if not is_all_a_symbol(code, exchange):
        return None
    return format_symbol(code, exchange)


def symbol_from_em(code: str, market_id: int) -> str | None:
    code = str(code).zfill(6)
    exchange = "SH" if market_id == 1 else ("BJ" if market_id == 2 else "SZ")
    if not is_all_a_symbol(code, exchange):
        return None
    return format_symbol(code, exchange)


def exchange_from_datacenter(row: dict) -> str:
    market = str(row.get("MARKET_CODE") or row.get("TRADE_MARKET") or "").upper()
    code = str(row.get("SECURITY_CODE") or row.get("SECUCODE", "").split(".")[0]).zfill(6)
    if "SH" in market or code.startswith(("60", "68")):
        return "SH"
    if "BJ" in market or code.startswith("92"):
        return "BJ"
    return "SZ"
