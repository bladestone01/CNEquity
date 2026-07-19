from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from ashare_lake.adapters.eastmoney.em_auth import EastMoneyClient
from ashare_lake.adapters.eastmoney.stock_news import fetch_stock_news
from ashare_lake.config import Config

logger = logging.getLogger(__name__)


class OnDemandService:
    """Fetch high-churn per-symbol data on first query and cache locally."""

    def __init__(self, config: Config):
        self.config = config
        self.cache_root = config.meta_root / "on_demand"
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, dataset: str, symbol: str) -> Path:
        safe = symbol.replace(".", "_")
        return self.cache_root / dataset / f"{safe}.json"

    def fetch(self, dataset: str, symbol: str, **kwargs) -> dict:
        if dataset not in self.config.on_demand_datasets and self.config.on_demand_datasets:
            raise ValueError(f"Dataset {dataset} not enabled for on-demand")

        path = self._cache_path(dataset, symbol)
        if path.exists() and not kwargs.get("refresh"):
            with open(path, encoding="utf-8") as f:
                return json.load(f)

        payload = self._fetch_remote(dataset, symbol, **kwargs)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return payload

    def _fetch_remote(self, dataset: str, symbol: str, **kwargs) -> dict:
        if dataset == "announcement_body":
            return self._fetch_announcement_body(symbol)
        if dataset == "stock_news":
            return self._fetch_stock_news(symbol, **kwargs)
        if dataset == "research_reports":
            return self._fetch_research_reports(symbol)
        if dataset == "financial_reports":
            return {"symbol": symbol, "statements": [], "source": "placeholder"}
        return {"dataset": dataset, "symbol": symbol, "status": "not_implemented"}

    def _fetch_stock_news(self, symbol: str, **kwargs) -> dict:
        if not self.config.sources.get("eastmoney", True):
            raise RuntimeError("stock_news: eastmoney source disabled in config")
        on_date = kwargs.get("on_date")
        if isinstance(on_date, str):
            from datetime import date

            on_date = date.fromisoformat(on_date)
        limit = int(kwargs.get("limit", 30))
        use_snownlp = bool(kwargs.get("use_snownlp", self.config.sentiment_use_snownlp))
        self.config.rate_limit("eastmoney")
        payload = fetch_stock_news(
            symbol,
            on_date=on_date,
            limit=limit,
            use_snownlp=use_snownlp,
        )
        payload["data_version"] = "v1"
        payload["fetched_at"] = datetime.now(UTC).isoformat()
        return payload

    def _fetch_announcement_body(self, symbol: str) -> dict:
        # TODO: implement cninfo fetch via https://www.cninfo.com.cn/new/hisAnnouncement/query
        code = symbol.split(".")[0]
        logger.info("On-demand announcement_body for %s (cninfo)", symbol)
        return {"symbol": symbol, "code": code, "items": [], "source": "cninfo"}

    def _fetch_research_reports(self, symbol: str) -> dict:
        code = symbol.split(".")[0]
        url = f"https://reportapi.eastmoney.com/report/list?code={code}&pageSize=10"
        try:
            with EastMoneyClient(
                min_interval=self.config.source_intervals.get("eastmoney", 1.0)
            ) as client:
                resp = client.get(url)
                data = resp.json()
                return {"symbol": symbol, "items": data, "source": "eastmoney"}
        except Exception as exc:
            logger.warning("research_reports fetch failed: %s", exc)
            return {"symbol": symbol, "items": [], "error": str(exc), "source": "eastmoney"}
