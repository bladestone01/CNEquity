"""EastMoney datacenter pagination helper."""

from __future__ import annotations

import logging
from urllib.parse import quote

from stock_data_engine.adapters.eastmoney.common import DATACENTER_BASE
from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

logger = logging.getLogger(__name__)


def fetch_datacenter(
    client: EastMoneyClient,
    report: str,
    columns: str,
    *,
    filter_expr: str = "",
    page_size: int = 5000,
    sort_columns: str | None = None,
    sort_types: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        params = (
            f"reportName={report}"
            f"&columns={quote(columns, safe=',')}"
            f"&pageSize={page_size}"
            f"&pageNumber={page}"
        )
        if filter_expr:
            params += f"&filter={quote(filter_expr, safe='()>=<=')}"
        if sort_columns:
            params += f"&sortColumns={sort_columns}&sortTypes={sort_types or '-1'}"
        url = f"{DATACENTER_BASE}?{params}"
        try:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning("EastMoney datacenter %s page %s failed: %s", report, page, exc)
            break

        batch = (payload.get("result") or {}).get("data") or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return rows
