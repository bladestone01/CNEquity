"""EastMoney F10 shareholder structure — 股本结构 / 股东户数 / 前十大股东.

Three datasets the long-format ``financial_statement_items`` cannot hold.
``top_holders`` is the clearest case: it is a ranked repeating group of ten per
period, and no number of ``item_code`` rows expresses a rank. The other two are
wide fixed records that would only be item codes by accident of shape.

Every report here is fetched **per report period**, not per symbol. One period
of 前十大流通股东 is ~55k rows across the market, so a per-symbol sweep would be
~5,500 requests where one filtered sweep is ~110 pages. These are the heaviest
datacenter consumers in the project.

PIT. A 半年报 shareholder list is dated 06-30 and disclosed in late August, so
keying it by period alone lets a July backtest read August's filing. Three of
the four reports carry ``NOTICE_DATE``; ``RPT_F10_EH_HOLDERS`` does not, so its
announce date is joined from ``RPT_F10_EH_FREEHOLDERS`` on (symbol, period) —
the two are halves of the same filing and share a disclosure date by
construction. Rows that find no match are dropped rather than dated with a
guess, and the count is logged.
"""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from ashare_lake.adapters.eastmoney.common import symbol_from_secucode
from ashare_lake.adapters.eastmoney.datacenter import fetch_datacenter
from ashare_lake.adapters.eastmoney.em_auth import EastMoneyClient
from ashare_lake.config import Config

logger = logging.getLogger(__name__)

_EQUITY_REPORT = "RPT_F10_EH_EQUITY"
_EQUITY_COLUMNS = (
    "SECUCODE,END_DATE,TOTAL_SHARES,LIMITED_SHARES,UNLIMITED_SHARES,"
    "FREELIQCI_SHARES,CHANGE_REASON,NOTICE_DATE"
)

_HOLDERNUM_REPORT = "RPT_F10_EH_HOLDERNUM"
_HOLDERNUM_COLUMNS = (
    "SECUCODE,END_DATE,HOLDER_TOTAL_NUM,TOTAL_NUM_RATIO,AVG_FREE_SHARES,AVG_HOLD_AMT,NOTICE_DATE"
)

# 前十大股东 (all shares). No NOTICE_DATE on this report — see module docstring.
_HOLDERS_REPORT = "RPT_F10_EH_HOLDERS"
_HOLDERS_COLUMNS = "SECUCODE,END_DATE,HOLDER_NAME,HOLD_NUM,HOLD_NUM_RATIO,HOLDER_RANK,IS_HOLDORG"

# 前十大流通股东 (float only). Carries NOTICE_DATE and a holder classification.
_FREEHOLDERS_REPORT = "RPT_F10_EH_FREEHOLDERS"
_FREEHOLDERS_COLUMNS = (
    "SECUCODE,END_DATE,HOLDER_NAME,HOLD_NUM,FREE_HOLDNUM_RATIO,HOLDER_RANK,"
    "IS_HOLDORG,HOLDER_TYPE,NOTICE_DATE"
)

SCOPE_TOTAL = "total"
SCOPE_FLOAT = "float"

# One period of 前十大流通股东 is ~55k rows = ~110 pages, and pageNumber is
# capped at 100 — see _MAX_PAGE_NUMBER in datacenter.py, which reports the cap
# as 服务器繁忙 and so looks exactly like throttling that patience would clear.
# It is not; waiting does nothing. Sorting by SECUCODE and handing the same
# column to keyset_column is what actually gets past page 100.
_KEYSET_COLUMN = "SECUCODE"

# Genuine busy answers do also happen on a sweep this long. A little more
# patience than the default 3/5s is cheap here because failing at page 90 throws
# away the 89 pages before it.
_SWEEP_RETRIES = 5
_SWEEP_BACKOFF_SECONDS = 15.0


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _em_date(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_org(value: object) -> bool | None:
    """IS_HOLDORG is "1"/"0" as a string; anything else is unknown, not False."""
    text = str(value or "").strip()
    if text == "1":
        return True
    if text == "0":
        return False
    return None


def _period_filter(period: date) -> str:
    return f"(END_DATE='{period.isoformat()}')"


def _fetch(
    client: EastMoneyClient,
    report: str,
    columns: str,
    period: date,
    *,
    config: Config | None,
) -> list[dict]:
    if config is not None:
        config.rate_limit("eastmoney")
    return fetch_datacenter(
        client,
        report,
        columns,
        filter_expr=_period_filter(period),
        # Ascending by the keyset column is a precondition of re-anchoring.
        sort_columns=_KEYSET_COLUMN,
        sort_types="1",
        keyset_column=_KEYSET_COLUMN,
        max_retries=_SWEEP_RETRIES,
        retry_backoff_seconds=_SWEEP_BACKOFF_SECONDS,
    )


def fetch_share_structure(
    period: date,
    *,
    client: EastMoneyClient | None = None,
    config: Config | None = None,
) -> pl.DataFrame:
    """股本结构 changes disclosed for *period*."""
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)
    try:
        raw = _fetch(client, _EQUITY_REPORT, _EQUITY_COLUMNS, period, config=config)
    finally:
        if owns:
            client.close()

    rows: list[dict] = []
    for item in raw:
        symbol = symbol_from_secucode(item.get("SECUCODE"))
        change_date = _em_date(item.get("END_DATE"))
        if not symbol or change_date is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "change_date": change_date,
                "total_shares": _num(item.get("TOTAL_SHARES")),
                "float_shares": _num(item.get("UNLIMITED_SHARES")),
                "restricted_shares": _num(item.get("LIMITED_SHARES")),
                "free_float_shares": _num(item.get("FREELIQCI_SHARES")),
                "change_reason": str(item.get("CHANGE_REASON") or "") or None,
                "announce_date": _em_date(item.get("NOTICE_DATE")),
            }
        )
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(subset=["symbol", "change_date", "announce_date"], keep="last")


def fetch_shareholder_counts(
    period: date,
    *,
    client: EastMoneyClient | None = None,
    config: Config | None = None,
) -> pl.DataFrame:
    """股东户数 for *period*."""
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)
    try:
        raw = _fetch(client, _HOLDERNUM_REPORT, _HOLDERNUM_COLUMNS, period, config=config)
    finally:
        if owns:
            client.close()

    rows: list[dict] = []
    for item in raw:
        symbol = symbol_from_secucode(item.get("SECUCODE"))
        end = _em_date(item.get("END_DATE"))
        if not symbol or end is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "report_period": end.isoformat(),
                "holder_count": _num(item.get("HOLDER_TOTAL_NUM")),
                "holder_count_change_pct": _num(item.get("TOTAL_NUM_RATIO")),
                "avg_float_shares": _num(item.get("AVG_FREE_SHARES")),
                "avg_holding_value": _num(item.get("AVG_HOLD_AMT")),
                "announce_date": _em_date(item.get("NOTICE_DATE")),
            }
        )
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).unique(
        subset=["symbol", "report_period", "announce_date"], keep="last"
    )


def _holder_rows(
    raw: list[dict],
    *,
    scope: str,
    pct_field: str,
    period_label: str,
) -> list[dict]:
    rows: list[dict] = []
    for item in raw:
        symbol = symbol_from_secucode(item.get("SECUCODE"))
        end = _em_date(item.get("END_DATE"))
        rank = item.get("HOLDER_RANK")
        if not symbol or end is None or rank is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "report_period": end.isoformat(),
                "holder_scope": scope,
                "holder_rank": int(rank),
                "holder_name": str(item.get("HOLDER_NAME") or "") or None,
                "holding_shares": _num(item.get("HOLD_NUM")),
                "holding_pct": _num(item.get(pct_field)),
                "is_institution": _is_org(item.get("IS_HOLDORG")),
                "holder_type": str(item.get("HOLDER_TYPE") or "") or None,
                "announce_date": _em_date(item.get("NOTICE_DATE")),
            }
        )
    logger.debug("top_holders %s %s: %d row(s)", scope, period_label, len(rows))
    return rows


def fetch_top_holders(
    period: date,
    *,
    client: EastMoneyClient | None = None,
    config: Config | None = None,
) -> pl.DataFrame:
    """前十大股东 + 前十大流通股东 for *period*, one frame.

    The float report is fetched first because it carries the disclosure date
    the total report omits; the total rows take theirs from it by
    (symbol, period).
    """
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)
    try:
        free_raw = _fetch(client, _FREEHOLDERS_REPORT, _FREEHOLDERS_COLUMNS, period, config=config)
        total_raw = _fetch(client, _HOLDERS_REPORT, _HOLDERS_COLUMNS, period, config=config)
    finally:
        if owns:
            client.close()

    label = period.isoformat()
    free_rows = _holder_rows(
        free_raw, scope=SCOPE_FLOAT, pct_field="FREE_HOLDNUM_RATIO", period_label=label
    )
    total_rows = _holder_rows(
        total_raw, scope=SCOPE_TOTAL, pct_field="HOLD_NUM_RATIO", period_label=label
    )

    frames: list[pl.DataFrame] = []
    if free_rows:
        frames.append(pl.DataFrame(free_rows))

    if total_rows:
        total_df = pl.DataFrame(total_rows)
        # Borrow the disclosure date: RPT_F10_EH_HOLDERS carries none, and the
        # two reports are halves of one filing.
        if free_rows:
            notices = (
                pl.DataFrame(free_rows)
                .select("symbol", "report_period", "announce_date")
                .drop_nulls("announce_date")
                .unique(subset=["symbol", "report_period"], keep="last")
                .rename({"announce_date": "_notice"})
            )
            total_df = total_df.join(notices, on=["symbol", "report_period"], how="left")
            total_df = total_df.with_columns(
                pl.col("announce_date").fill_null(pl.col("_notice"))
            ).drop("_notice")
        # A row with no disclosure date cannot be served point-in-time. Dropping
        # is the honest option: dating it with the period end would assert the
        # list was known on 06-30, which is the exact lookahead this is for.
        undated = total_df.filter(pl.col("announce_date").is_null()).height
        if undated:
            logger.info(
                "top_holders %s: dropping %d total-scope row(s) with no disclosure date "
                "(no matching float-holder filing to borrow one from)",
                label,
                undated,
            )
            total_df = total_df.drop_nulls("announce_date")
        if total_df.height:
            frames.append(total_df)

    if not frames:
        return pl.DataFrame()
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .unique(
            subset=["symbol", "report_period", "holder_scope", "holder_rank", "announce_date"],
            keep="last",
        )
        .sort(["report_period", "symbol", "holder_scope", "holder_rank"])
    )
