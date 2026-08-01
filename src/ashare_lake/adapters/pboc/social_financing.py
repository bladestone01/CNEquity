"""中国人民银行 调查统计司 — 社会融资规模增量统计表.

https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html

社会融资规模 is a PBOC statistic. This reads it from the PBOC's own published
statistical table rather than from a republisher, for a reason measured on
2026-08-01 rather than assumed: MOFCOM, which this replaces, was serving
2026-04 as 6245 after the PBOC had revised it to 6238, and its newest month was
2026-04 while the PBOC had published through 2026-06. Summing the PBOC series
for 2026-01..04 gives 154,500 — exactly the 15.45万亿 the PBOC states in prose —
while the republisher's copy summed to 154,507. The intermediary was propagating
a superseded vintage, so it is not a safe backup either (ADR-0003: a backup must
not silently write canonical).

The table is an Excel attachment, not prose: bilingual headers, an explicit
``单位：亿元人民币``, one row per month. Older years ship ``.xls``, recent ones
``.xlsx``; the layout is the same and this project already carries pandas,
openpyxl and xlrd to parse the Shenwan / CNI constituent workbooks.

Discovery is three hops, each keyed on a stable Chinese label rather than on a
numeric section id (those change every year):

1. the statistics index lists ``<YYYY>年统计数据`` → that year's section
2. the year section links ``社会融资规模`` → that year's sub-section
3. the sub-section links the ``社会融资规模增量统计表`` workbook
"""

from __future__ import annotations

import calendar
import io
import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

BASE = "https://www.pbc.gov.cn"
STATS_INDEX = f"{BASE}/diaochatongjisi/116219/116319/index.html"
_TIMEOUT_SECONDS = 60.0

# The site mixes single- and double-quoted attributes within one page, so every
# href pattern here accepts either.
_YEAR_SECTION_RE = re.compile(r"""href=["']([^"']+)["'][^>]*>\s*(\d{4})年统计数据\s*</a>""")
_AFRE_SECTION_RE = re.compile(r"""href=["']([^"']+)["'][^>]*>\s*社会融资规模\s*</a>""")
_TABLE_LABEL = "社会融资规模增量统计表"
_WORKBOOK_RE = re.compile(r"""href=["']([^"']+\.xlsx?)["']""")
# The workbook link sits within the same layout block as its label; bound the
# search so a later table's attachment cannot be picked up by mistake.
_LABEL_WINDOW = 1200

# Month cells arrive as "2026.01" (str) in the legacy .xls and as the float
# 2026.01 in .xlsx — where October becomes 2026.1, which formats back to
# "2026.10" only at two decimals. Anything else is a note or a blank row.
_MONTH_RE = re.compile(r"^(\d{4})\.(\d{1,2})$")


def _client():
    # Chrome impersonation: the site is served behind a WAF that drops a plain
    # httpx handshake on the attachment paths.
    from curl_cffi import requests as cr

    return cr


def _get(url: str) -> str:
    resp = _client().get(url, impersonate="chrome", timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _absolute(href: str) -> str:
    return href if href.startswith("http") else f"{BASE}{href}"


def year_sections(index_html: str | None = None) -> dict[int, str]:
    """Map year → that year's statistics section URL."""
    html = index_html if index_html is not None else _get(STATS_INDEX)
    return {int(year): _absolute(href) for href, year in _YEAR_SECTION_RE.findall(html)}


def _workbook_url(year_section_url: str) -> str | None:
    section_html = _get(year_section_url)
    match = _AFRE_SECTION_RE.search(section_html)
    if not match:
        return None
    afre_html = _get(_absolute(match.group(1)))
    label_at = afre_html.find(_TABLE_LABEL)
    if label_at < 0:
        return None
    link = _WORKBOOK_RE.search(afre_html[label_at : label_at + _LABEL_WINDOW])
    return _absolute(link.group(1)) if link else None


def _month_end(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, float):
        # 2026.1 is October, not January — two decimals disambiguate.
        text = f"{value:.2f}"
    else:
        text = str(value).strip()
    match = _MONTH_RE.match(text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_workbook(content: bytes) -> list[dict]:
    """Rows from one workbook as ``[{"obs_date", "value"}, ...]``.

    Column 0 is the month, column 1 the headline 增量; the remaining columns are
    its components (人民币贷款, 委托贷款, …) and are not read today. Header, note
    and not-yet-published rows fail the month parse and drop out.

    Some sheets stack more than one table — the 2019 workbook carries
    ``表1 …增量数据`` in 亿元 followed by ``表2 …增量占比数据`` in %, and both
    have a month column, so reading every month-shaped row pulled a column of
    literal ``100``s into the series. Each table declares its own ``单位`` line,
    so collection follows that declaration and stops as soon as the unit is no
    longer 亿元.
    """
    import pandas as pd

    pdf = pd.read_excel(io.BytesIO(content), header=None)
    rows: list[dict] = []
    in_yuan_table = False
    for record in pdf.itertuples(index=False):
        if len(record) < 2:
            continue
        header = "" if record[0] is None or pd.isna(record[0]) else str(record[0])
        if "单位" in header:
            in_yuan_table = "亿元" in header
            continue
        if not in_yuan_table:
            continue
        obs = _month_end(record[0])
        if obs is None:
            continue
        raw = record[1]
        if raw is None or pd.isna(raw):
            # A month the PBOC has not published yet — the row exists, blank.
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        rows.append({"obs_date": obs, "value": value})
    return rows


def fetch_social_financing(*, config=None, start_year: int = 2015) -> list[dict]:
    """社融增量, newest year first, from ``start_year`` to the latest published.

    Degrades to whatever it managed to read: this is one indicator on a
    multi-source dataset and a daily run should not fail over it. A year that
    cannot be reached is logged and skipped, so a later run fills the gap.
    """
    try:
        sections = year_sections()
    except Exception as exc:
        logger.warning("PBOC statistics index unavailable: %s", exc)
        return []

    # Newest year first, and the first reading of a month wins. Workbooks
    # overlap: the 2019 one restates 2017-2019 under the 完善后 caliber
    # (2017-01 = 37720) while the 2017 one still carries the original
    # 36970.49. The later publication is the current official series, so
    # descending order is load-bearing, not incidental.
    seen: set[date] = set()
    rows: list[dict] = []
    for year in sorted((y for y in sections if y >= start_year), reverse=True):
        if config is not None:
            config.rate_limit("pboc")
        try:
            workbook_url = _workbook_url(sections[year])
            if workbook_url is None:
                logger.warning("PBOC %s: no %s workbook link found", year, _TABLE_LABEL)
                continue
            content = (
                _client().get(workbook_url, impersonate="chrome", timeout=_TIMEOUT_SECONDS).content
            )
            year_rows = parse_workbook(content)
        except Exception as exc:
            logger.warning("PBOC social financing %s skipped: %s", year, exc)
            continue
        if not year_rows:
            logger.warning("PBOC %s workbook parsed to no rows; layout may have changed", year)
        for row in year_rows:
            if row["obs_date"] in seen:
                continue
            seen.add(row["obs_date"])
            rows.append(row)

    if not rows:
        logger.warning("PBOC social financing returned no usable rows")
    return rows
