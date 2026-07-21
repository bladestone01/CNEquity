"""EastMoney rotation datasets: hot rank, sector bars/flows, market news."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import polars as pl

from ashare_lake.adapters.eastmoney.clist import fetch_clist_pages
from ashare_lake.adapters.eastmoney.common import PUSH2HIS_KLINE_HOSTS, _to_float
from ashare_lake.adapters.eastmoney.em_auth import EastMoneyClient
from ashare_lake.domain.symbols import format_symbol

if TYPE_CHECKING:
    from ashare_lake.config import Config

logger = logging.getLogger(__name__)

_HOT_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
_NEWS_URL = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"

_CONCEPT_FS = "m:90+t:3"
_INDUSTRY_FS = "m:90+t:2"
_BOARD_FIELDS = "f12,f14,f2,f3,f15,f16,f17,f5,f6,f8,f62"
_BOARD_KLINE_PATH = "/api/qt/stock/kline/get"
# f51..f61: date,open,close,high,low,volume,amount,amplitude,change_pct,change,turnover
_KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
_KLINE_MAX_RETRIES = 3
_KLINE_RETRY_BACKOFF_SECONDS = 3.0
# Abort a dead egress early instead of hammering ~991 boards into a ban.
_EARLY_ABORT_AFTER_FAILURES = 15
# After a streak of transport failures, pause before the next board.
_STREAK_COOLDOWN_AFTER = 5
_STREAK_COOLDOWN_SECONDS = 90.0
_DEFAULT_BATCH_SIZE = 15
_DEFAULT_BATCH_REST = 60.0


def _hot_symbol(sc: str) -> str | None:
    text = str(sc or "").strip().upper()
    if len(text) < 8:
        return None
    mkt, code = text[:2], text[2:].zfill(6)
    if mkt not in {"SH", "SZ", "BJ"}:
        return None
    return format_symbol(code, mkt)


def _news_symbols(stock_list: list | None) -> str | None:
    if not stock_list:
        return None
    out: list[str] = []
    for raw in stock_list:
        text = str(raw)
        if "." in text:
            parts = text.split(".", 1)
            code = parts[1].zfill(6)
            mkt = "SH" if code.startswith(("60", "68")) else "SZ"
        else:
            code = text.zfill(6)
            mkt = "SH" if code.startswith(("60", "68")) else "SZ"
        out.append(format_symbol(code, mkt))
    return ",".join(sorted(set(out))) if out else None


def fetch_hot_rank(trade_date: date, *, top_n: int = 500) -> pl.DataFrame:
    rows: list[dict] = []
    page_size = 100
    page = 1
    with EastMoneyClient() as client:
        while len(rows) < top_n:
            body = json.dumps(
                {
                    "appId": "appId01",
                    "globalId": "786e4c21-70dc-435a-93bb-b",
                    "pageNo": page,
                    "pageSize": page_size,
                }
            ).encode()
            resp = client.post(
                _HOT_RANK_URL,
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("data") or []
            if not batch:
                break
            for item in batch:
                sym = _hot_symbol(item.get("sc", ""))
                if not sym:
                    continue
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": trade_date,
                        "rank": int(item.get("rk") or 0),
                        "rank_change": int(item.get("rc") or 0),
                        "hist_rank": int(item.get("hisRc") or 0),
                    }
                )
                if len(rows) >= top_n:
                    break
            if len(batch) < page_size:
                break
            page += 1
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _fetch_board_rows(client: EastMoneyClient, fs: str, board_type: str) -> list[dict]:
    # Smaller pages: pz=5000 often trips push2 502 mid-universe; 100 is stable.
    raw = fetch_clist_pages(client, fields=_BOARD_FIELDS, fs=fs, page_size=100)
    rows: list[dict] = []
    for item in raw:
        code = str(item.get("f12") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "sector_code": code,
                "sector_name": str(item.get("f14") or ""),
                "board_type": board_type,
                "item": item,
            }
        )
    return rows


def _dedupe_boards(boards: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for board in boards:
        code = board["sector_code"]
        if code in seen:
            continue
        seen.add(code)
        out.append(board)
    return out


def fetch_sector_bars(trade_date: date) -> pl.DataFrame:
    rows: list[dict] = []
    with EastMoneyClient() as client:
        boards = _fetch_board_rows(client, _CONCEPT_FS, "concept") + _fetch_board_rows(
            client, _INDUSTRY_FS, "industry"
        )
        for b in boards:
            item = b["item"]
            rows.append(
                {
                    "sector_code": b["sector_code"],
                    "sector_name": b["sector_name"],
                    "board_type": b["board_type"],
                    "trade_date": trade_date,
                    "open": _to_float(item.get("f17")),
                    "high": _to_float(item.get("f15")),
                    "low": _to_float(item.get("f16")),
                    "close": _to_float(item.get("f2")),
                    "volume": int(_to_float(item.get("f5"))),
                    "amount": _to_float(item.get("f6")),
                    "change_pct": _to_float(item.get("f3")),
                }
            )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _board_kline_params(board: dict, start: date, end: date) -> dict[str, str | int]:
    return {
        "secid": f"90.{board['sector_code']}",
        "klt": 101,  # daily
        "fqt": 0,  # board indices carry no corporate actions; raw == adjusted
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": _KLINE_FIELDS2,
        "smplmt": 10000,
        "lmt": 1000000,
    }


def _parse_board_klines(board: dict, klines: list) -> list[dict]:
    out: list[dict] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 9:
            continue
        out.append(
            {
                "sector_code": board["sector_code"],
                "sector_name": board["sector_name"],
                "board_type": board["board_type"],
                "trade_date": date.fromisoformat(parts[0]),
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": int(float(parts[5])),
                "amount": float(parts[6]),
                "change_pct": float(parts[8]),
            }
        )
    return out


def _fetch_board_kline_rows(
    client: EastMoneyClient,
    board: dict,
    start: date,
    end: date,
    *,
    max_retries: int = _KLINE_MAX_RETRIES,
    retry_backoff_seconds: float = _KLINE_RETRY_BACKOFF_SECONDS,
) -> list[dict]:
    params = _board_kline_params(board, start, end)
    last_exc: Exception | None = None
    for host in PUSH2HIS_KLINE_HOSTS:
        url = f"{host}{_BOARD_KLINE_PATH}"
        for attempt in range(max_retries):
            try:
                resp = client.get(url, params=params, timeout=30.0)
                resp.raise_for_status()
                klines = ((resp.json().get("data") or {}).get("klines")) or []
                return _parse_board_klines(board, klines)
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < max_retries:
                    time.sleep(retry_backoff_seconds * (attempt + 1))
    raise RuntimeError(
        f"sector kline failed for {board['sector_code']} on all push2his hosts"
    ) from last_exc


def _boards_from_lake(config: Config) -> list[dict]:
    """Board catalog from curated sector_bars when live clist is unavailable."""
    root = config.data_root / "curated" / "sector_bars"
    if not root.exists():
        return []
    try:
        df = (
            pl.scan_parquet(str(root / "**" / "*.parquet"))
            .select("sector_code", "sector_name", "board_type")
            .unique()
            .collect()
        )
    except Exception:
        return []
    if df.is_empty():
        return []
    out: list[dict] = []
    for row in df.iter_rows(named=True):
        code = str(row.get("sector_code") or "").strip()
        if not code:
            continue
        out.append(
            {
                "sector_code": code,
                "sector_name": str(row.get("sector_name") or ""),
                "board_type": str(row.get("board_type") or "concept"),
                "item": {},
            }
        )
    return out


def fetch_sector_bars_history(
    start: date,
    end: date,
    *,
    config: Config | None = None,
    skip_sectors: set[str] | None = None,
    only_sectors: set[str] | None = None,
) -> tuple[pl.DataFrame, list[str], list[str]]:
    """Historical daily board bars via the EastMoney kline API (secid ``90.BKxxxx``).

    The daily clist snapshot only sees today, so ``asl backfill sector_bars``
    replays history through this path instead. Returns
    ``(frame, failed_sector_codes, succeeded_sector_codes)`` — partial sweeps
    must be visible to the caller (audit finding), never silently dropped.

    Board catalog prefers live clist; on clist failure (overseas / 502), falls
    back to distinct boards already in curated ``sector_bars`` so a kline-only
    path still works under mainland egress.
    """
    rows: list[dict] = []
    failed: list[str] = []
    succeeded: list[str] = []
    skip = skip_sectors or set()
    client_kwargs: dict = {"config": config} if config is not None else {"min_interval": 0.3}
    with EastMoneyClient(**client_kwargs) as client:
        boards: list[dict] = []
        try:
            boards = _dedupe_boards(
                _fetch_board_rows(client, _CONCEPT_FS, "concept")
                + _fetch_board_rows(client, _INDUSTRY_FS, "industry")
            )
        except Exception as exc:
            logger.warning("sector_bars history: live clist failed (%s); trying lake catalog", exc)
            if config is not None:
                boards = _dedupe_boards(_boards_from_lake(config))
            if not boards:
                raise
            logger.info("sector_bars history: using %d boards from lake catalog", len(boards))
        todo = [b for b in boards if b["sector_code"] not in skip]
        if only_sectors is not None:
            todo = [b for b in todo if b["sector_code"] in only_sectors]
        batch = (
            int(getattr(config, "eastmoney_batch_size", _DEFAULT_BATCH_SIZE))
            if config is not None
            else _DEFAULT_BATCH_SIZE
        )
        rest = (
            float(getattr(config, "eastmoney_batch_rest_seconds", _DEFAULT_BATCH_REST))
            if config is not None
            else _DEFAULT_BATCH_REST
        )
        fail_streak = 0
        for i, board in enumerate(todo):
            if batch > 0 and rest > 0 and i > 0 and i % batch == 0:
                logger.info(
                    "sector_bars history: batch rest %.0fs after %d boards "
                    "(ok=%d failed=%d)",
                    rest,
                    i,
                    len(succeeded),
                    len(failed),
                )
                time.sleep(rest)
            try:
                rows.extend(_fetch_board_kline_rows(client, board, start, end))
                succeeded.append(board["sector_code"])
                fail_streak = 0
            except Exception as exc:
                logger.warning(
                    "sector_bars history: %s (%s) failed: %s: %s",
                    board["sector_code"],
                    board["sector_name"],
                    type(exc).__name__,
                    exc,
                )
                failed.append(board["sector_code"])
                fail_streak += 1
                if fail_streak >= _STREAK_COOLDOWN_AFTER:
                    logger.warning(
                        "sector_bars history: %d consecutive failures — "
                        "cooling down %.0fs",
                        fail_streak,
                        _STREAK_COOLDOWN_SECONDS,
                    )
                    time.sleep(_STREAK_COOLDOWN_SECONDS)
                    fail_streak = 0
                if (
                    not succeeded
                    and len(failed) >= _EARLY_ABORT_AFTER_FAILURES
                ):
                    remaining = [b["sector_code"] for b in todo[i + 1 :]]
                    logger.error(
                        "sector_bars history: aborting — first %d boards failed "
                        "(push2his unreachable); %d left unmarked for retry",
                        len(failed),
                        len(remaining),
                    )
                    # Leave remaining unmarked so --retry-failed / next --force
                    # can resume; do not append them to failed (would pollute
                    # checkpoint as permanent misses).
                    break
    return (pl.DataFrame(rows) if rows else pl.DataFrame()), failed, succeeded


def fetch_sector_fund_flow(trade_date: date) -> pl.DataFrame:
    rows: list[dict] = []
    with EastMoneyClient() as client:
        boards = _fetch_board_rows(client, _CONCEPT_FS, "concept") + _fetch_board_rows(
            client, _INDUSTRY_FS, "industry"
        )
        for b in boards:
            item = b["item"]
            rows.append(
                {
                    "sector_code": b["sector_code"],
                    "sector_name": b["sector_name"],
                    "board_type": b["board_type"],
                    "trade_date": trade_date,
                    "main_net_inflow": _to_float(item.get("f62")),
                    "change_pct": _to_float(item.get("f3")),
                    "turnover_pct": _to_float(item.get("f8")),
                }
            )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_news_headlines(trade_date: date, *, page_size: int = 200) -> pl.DataFrame:
    rows: list[dict] = []
    target = trade_date.isoformat()
    with EastMoneyClient() as client:
        params = urlencode(
            {
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": page_size,
                "req_trace": "1",
            }
        )
        resp = client.get(f"{_NEWS_URL}?{params}", timeout=30.0)
        resp.raise_for_status()
        items = (resp.json().get("data") or {}).get("fastNewsList") or []
        for item in items:
            show_time = str(item.get("showTime") or "")
            if not show_time.startswith(target):
                continue
            pub_dt = datetime.fromisoformat(show_time)
            rows.append(
                {
                    "news_id": str(item.get("code") or item.get("realSort") or ""),
                    "publish_date": trade_date,
                    "publish_time": pub_dt.strftime("%H:%M:%S"),
                    "title": str(item.get("title") or "").strip(),
                    "summary": str(item.get("summary") or "").strip() or None,
                    "related_symbols": _news_symbols(item.get("stockList")),
                    "channel": "fast_news",
                }
            )
    if not rows:
        logger.info("news_headlines: no items for %s (market may be closed)", target)
    return pl.DataFrame(rows) if rows else pl.DataFrame()
