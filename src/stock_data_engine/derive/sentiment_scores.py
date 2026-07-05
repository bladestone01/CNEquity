"""Batch sentiment scores from announcements + stock news NLP."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from stock_data_engine.adapters.eastmoney.stock_news import fetch_stock_news
from stock_data_engine.config import Config
from stock_data_engine.domain.sentiment import score_text

_ANNOUNCEMENT_CHANNEL = "announcement_keywords"
_NEWS_CHANNEL = "stock_news_nlp"
_DEFAULT_NEWS_SYMBOL_LIMIT = 300


def _read_announcements(root: Path, trade_date: date) -> pl.DataFrame:
    if not root.exists():
        return pl.DataFrame()
    files = list(root.glob(f"announce_date={trade_date.isoformat()}/**/*.parquet"))
    if not files:
        files = list(root.glob("**/*.parquet"))
    if not files:
        return pl.DataFrame()
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    if "announce_date" not in df.columns:
        return pl.DataFrame()
    return df.filter(pl.col("announce_date") == trade_date)


def _announcement_sentiment(config: Config, trade_date: date) -> pl.DataFrame:
    root = config.curated_root / "announcement_index"
    announcements = _read_announcements(root, trade_date)
    if announcements.is_empty() or "title" not in announcements.columns:
        return pl.DataFrame()

    scored = announcements.with_columns(
        pl.col("title")
        .map_elements(
            lambda t: score_text(str(t), use_snownlp=False)[0],
            return_dtype=pl.Float64,
        )
        .alias("_score")
    )
    agg = scored.group_by("symbol").agg(
        pl.col("_score").mean().alias("sentiment_score"),
        pl.len().alias("headline_count"),
    )
    return agg.with_columns(
        pl.lit(trade_date).alias("trade_date"),
        pl.lit(_ANNOUNCEMENT_CHANNEL).alias("score_channel"),
    ).select(["symbol", "trade_date", "score_channel", "sentiment_score", "headline_count"])


def _news_sentiment_symbols(config: Config, trade_date: date, limit: int) -> list[str]:
    ann = _read_announcements(config.curated_root / "announcement_index", trade_date)
    symbols: list[str] = []
    if not ann.is_empty() and "symbol" in ann.columns:
        symbols = ann["symbol"].unique().to_list()
    if len(symbols) >= limit:
        return symbols[:limit]

    bars_root = config.curated_root / "daily_bars"
    if bars_root.exists():
        files = list(bars_root.glob(f"trade_date={trade_date.isoformat()}/**/*.parquet"))
        if files:
            bars = pl.read_parquet(files[0])
            if "symbol" in bars.columns and "amount" in bars.columns:
                top = (
                    bars.sort("amount", descending=True)
                    .head(max(0, limit - len(symbols)))
                    ["symbol"]
                    .to_list()
                )
                for sym in top:
                    if sym not in symbols:
                        symbols.append(sym)
    return symbols[:limit]


def _stock_news_sentiment(config: Config, trade_date: date) -> pl.DataFrame:
    if not config.sources.get("eastmoney", True):
        return pl.DataFrame()

    limit = config.sentiment_news_symbol_limit
    symbols = _news_sentiment_symbols(config, trade_date, limit)
    if not symbols:
        return pl.DataFrame()

    rows: list[dict] = []
    use_snownlp = config.sentiment_use_snownlp
    client = None
    try:
        from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient

        client = EastMoneyClient(
            min_interval=config.source_intervals.get("eastmoney", 1.0)
        )
        for sym in symbols:
            config.rate_limit("eastmoney")
            payload = fetch_stock_news(
                sym,
                on_date=trade_date,
                limit=20,
                use_snownlp=use_snownlp,
                client=client,
            )
            if payload.get("headline_count", 0) == 0:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": trade_date,
                    "score_channel": _NEWS_CHANNEL,
                    "sentiment_score": payload["aggregate_sentiment"],
                    "headline_count": payload["headline_count"],
                }
            )
            _maybe_cache_news(config, payload)
    finally:
        if client is not None:
            client.close()

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows)


def _maybe_cache_news(config: Config, payload: dict) -> None:
    """Warm on-demand cache when batch fetch pulls news."""
    if not config.on_demand_enabled:
        return
    import json

    symbol = payload["symbol"]
    cache_dir = config.meta_root / "on_demand" / "stock_news"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol.replace('.', '_')}.json"
    if path.exists():
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def compute_sentiment_scores(config: Config, trade_date: date) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    ann = _announcement_sentiment(config, trade_date)
    if not ann.is_empty():
        frames.append(ann)
    news = _stock_news_sentiment(config, trade_date)
    if not news.is_empty():
        frames.append(news)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")
