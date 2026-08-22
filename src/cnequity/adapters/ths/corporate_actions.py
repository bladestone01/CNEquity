"""同花顺企业行动页面，用于显式修复历史 BJ 除权除息事件。

同花顺的 ``basic`` 页面保留了不少已经退市的北交所/新三板旧代码，且表格
直接给出 A 股除权除息日。这不是日更主源，也不应悄悄改变普通回填结果；调用方
必须显式开启 repair flag，并保留 ``source=ths`` provenance。
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import date
from html.parser import HTMLParser

import httpx
import polars as pl

from cnequity.config import Config
from cnequity.domain.symbols import parse_symbol

logger = logging.getLogger(__name__)

__all__ = ["fetch_corporate_actions_ths"]


_OUTPUT_SCHEMA = {
    "symbol": pl.Utf8,
    "ex_date": pl.Date,
    "action_type": pl.Utf8,
    "cash_dividend": pl.Float64,
    "bonus_ratio": pl.Float64,
    "transfer_ratio": pl.Float64,
    "allotment_ratio": pl.Float64,
    "allotment_price": pl.Float64,
}

_URL = "https://basic.10jqka.com.cn/{code}/bonus.html"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://basic.10jqka.com.cn/"}
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MIN_INTERVAL = 3.0
_MAX_RETRIES = 3

# The first component is usually written as ``10送3股`` while subsequent
# components omit the repeated base, e.g. ``10转4股派4.50元``. Make the
# ``10`` prefix optional after the first match so both forms are expanded.
_PLAN_RE = re.compile(r"(?:10\s*股?\s*)?(送|转增?|派|配)\s*([0-9]+(?:\.[0-9]+)?)")
_ALLOTMENT_PRICE_RE = re.compile(r"配股(?:价|价格)\s*([0-9]+(?:\.[0-9]+)?)\s*元?")


class ThsCorporateActionsError(RuntimeError):
    """同花顺企业行动页面无法读取或无法识别。"""


class _BonusTableParser(HTMLParser):
    """Collect cell text from every HTML table row without an lxml dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._tr_depth = 0
        self._cell_open = False
        self._cell_text: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._tr_depth += 1
            if self._tr_depth == 1:
                self._row = []
        elif self._tr_depth == 1 and tag in ("td", "th"):
            self._cell_open = True
            self._cell_text = []

    def handle_data(self, data: str) -> None:
        if self._tr_depth == 1 and self._cell_open:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._tr_depth == 1 and tag in ("td", "th") and self._cell_open:
            self._row.append(" ".join("".join(self._cell_text).split()))
            self._cell_open = False
            self._cell_text = []
        elif tag == "tr" and self._tr_depth:
            if self._tr_depth == 1 and self._row:
                self.rows.append(self._row)
            self._tr_depth -= 1


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_OUTPUT_SCHEMA)


def _parse_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if not text or text in {"--", "-", "—"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_plan(plan: str) -> dict[str, float | None]:
    """Convert a per-10-share Chinese plan into per-share fields."""
    values: dict[str, float | None] = {
        "cash_dividend": 0.0,
        "bonus_ratio": 0.0,
        "transfer_ratio": 0.0,
        "allotment_ratio": 0.0,
        "allotment_price": None,
    }
    field_by_token = {
        "派": "cash_dividend",
        "送": "bonus_ratio",
        "转": "transfer_ratio",
        "转增": "transfer_ratio",
        "配": "allotment_ratio",
    }
    for token, raw_value in _PLAN_RE.findall(plan or ""):
        try:
            value = float(raw_value) / 10.0
        except ValueError:
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        field = field_by_token[token]
        values[field] = float(values[field] or 0.0) + value
    price_match = _ALLOTMENT_PRICE_RE.search(plan or "")
    if price_match:
        try:
            price = float(price_match.group(1))
        except ValueError:
            price = None
        if price is not None and math.isfinite(price) and price > 0:
            values["allotment_price"] = price
    return values


def _rows_from_page(symbol: str, html: str, start: date, end: date) -> list[dict]:
    """Parse completed plans from one bonus page within the requested window."""
    parser = _BonusTableParser()
    parser.feed(html)
    rows: list[dict] = []
    for cells in parser.rows:
        # The current table has 11 columns. Keep the positional contract
        # anchored to the visible header: plan text is index 4, A-share ex-date
        # index 6, and progress index 8. Rows from unrelated page tables are
        # ignored by the progress/date/action checks below.
        if len(cells) < 9 or not cells[8].startswith("实施方案"):
            continue
        ex_date = _parse_date(cells[6])
        if ex_date is None or not (start <= ex_date <= end):
            continue
        values = _parse_plan(cells[4])
        parsed = {
            "cash_dividend": float(values["cash_dividend"] or 0.0),
            "bonus_ratio": float(values["bonus_ratio"] or 0.0),
            "transfer_ratio": float(values["transfer_ratio"] or 0.0),
            "allotment_ratio": float(values["allotment_ratio"] or 0.0),
            "allotment_price": values["allotment_price"],
        }
        common = {
            "symbol": symbol,
            "ex_date": ex_date,
            "cash_dividend": 0.0,
            "bonus_ratio": 0.0,
            "transfer_ratio": 0.0,
            "allotment_ratio": 0.0,
            "allotment_price": None,
        }
        if parsed["cash_dividend"] > 0:
            rows.append({**common, "action_type": "cash_dividend", "cash_dividend": parsed["cash_dividend"]})
        if parsed["bonus_ratio"] > 0:
            rows.append({**common, "action_type": "bonus", "bonus_ratio": parsed["bonus_ratio"]})
        if parsed["transfer_ratio"] > 0:
            rows.append({**common, "action_type": "transfer", "transfer_ratio": parsed["transfer_ratio"]})
        if parsed["allotment_ratio"] > 0:
            rows.append(
                {
                    **common,
                    "action_type": "allotment",
                    "allotment_ratio": parsed["allotment_ratio"],
                    "allotment_price": parsed["allotment_price"],
                }
            )
    return rows


def _fetch_page(code: str, *, config: Config | None = None) -> str:
    """Fetch and decode one page; the endpoint is GB18030 despite weak headers."""
    retries = config.max_retries if config is not None else _MAX_RETRIES
    backoff = float(config.retry_backoff_seconds if config is not None else 2.0)
    timeout = _DEFAULT_TIMEOUT
    last_exc: Exception | None = None
    url = _URL.format(code=code)
    for attempt in range(max(1, retries)):
        if config is not None:
            config.rate_limit("ths_bonus")
        else:
            time.sleep(_DEFAULT_MIN_INTERVAL)
        try:
            response = httpx.get(
                url,
                headers=_HEADERS,
                timeout=timeout,
                follow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001 — retry and report symbol failure
            last_exc = exc
        else:
            if response.status_code == 200:
                return response.content.decode("gb18030", errors="ignore")
            if response.status_code in (401, 403):
                raise ThsCorporateActionsError(
                    f"{url} -> HTTP {response.status_code} (token-gated endpoint)"
                )
            if response.status_code == 404:
                raise ThsCorporateActionsError(f"{url} -> HTTP 404 (page not found)")
            last_exc = ThsCorporateActionsError(f"{url} -> HTTP {response.status_code}")
        if attempt + 1 < max(1, retries):
            time.sleep(backoff * (attempt + 1))
    raise ThsCorporateActionsError(f"{url} failed after {max(1, retries)} attempts") from last_exc


def fetch_corporate_actions_ths(
    symbols: list[str],
    start: date,
    end: date,
    *,
    config: Config | None = None,
    symbol_windows: Mapping[str, tuple[date, date]] | None = None,
    page_fetcher: Callable[[str], str] | None = None,
) -> tuple[pl.DataFrame, list[str]]:
    """Fetch explicit per-symbol THS repairs and return ``(rows, failures)``.

    Only BJ symbols are queried. ``symbol_windows`` lets the caller bound the
    request to each instrument's actual listing/delisting interval so an old
    page cannot create events outside the symbol's observed life.
    """
    rows: list[dict] = []
    failed: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        if symbol in seen:
            continue
        seen.add(symbol)
        try:
            info = parse_symbol(symbol)
        except ValueError:
            logger.warning("ths corporate_actions: skipping invalid symbol %s", symbol)
            continue
        if info.exchange != "BJ":
            continue
        window = symbol_windows.get(symbol, (start, end)) if symbol_windows else (start, end)
        if window[0] > window[1]:
            continue
        try:
            html = page_fetcher(info.code) if page_fetcher is not None else _fetch_page(
                info.code, config=config
            )
            rows.extend(_rows_from_page(symbol, html, window[0], window[1]))
        except Exception as exc:  # noqa: BLE001 — preserve other symbols for retry
            failed.append(symbol)
            logger.warning("ths corporate_actions: failed for %s: %s", symbol, exc)

    if not rows:
        return _empty(), failed
    frame = pl.DataFrame(rows, schema_overrides={"allotment_price": pl.Float64})
    return (
        frame.unique(subset=["symbol", "ex_date", "action_type"], keep="last").sort(
            ["ex_date", "symbol", "action_type"]
        ),
        failed,
    )
