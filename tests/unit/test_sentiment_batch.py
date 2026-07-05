from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from stock_data_engine.config import Config
from stock_data_engine.derive.sentiment_scores import compute_sentiment_scores


@pytest.fixture
def news_batch_lake(tmp_path):
    root = tmp_path / "data"
    ann = root / "curated" / "announcement_index" / "announce_date=2024-06-28"
    ann.mkdir(parents=True)
    pl.DataFrame(
        {
            "announcement_id": ["a1"],
            "symbol": ["600519.SH"],
            "title": ["业绩超预期增长"],
            "announce_date": [date(2024, 6, 28)],
            "category": [""],
            "url": [""],
            "source": ["cninfo"],
            "data_version": ["v1"],
            "fetched_at": ["2024-06-28T00:00:00+00:00"],
        }
    ).write_parquet(ann / "part-0.parquet")
    return Config(data_root=root, sources={"eastmoney": True}, sentiment_use_snownlp=False)


def test_batch_sentiment_includes_stock_news_channel(news_batch_lake):
    news_payload = {
        "symbol": "600519.SH",
        "source": "eastmoney",
        "items": [{"title": "签约利好", "sentiment_score": 0.8}],
        "headline_count": 1,
        "aggregate_sentiment": 0.8,
    }
    with patch(
        "stock_data_engine.derive.sentiment_scores.fetch_stock_news",
        return_value=news_payload,
    ):
        df = compute_sentiment_scores(news_batch_lake, date(2024, 6, 28))

    channels = set(df["score_channel"].to_list())
    assert "announcement_keywords" in channels
    assert "stock_news_nlp" in channels
    news = df.filter(pl.col("score_channel") == "stock_news_nlp")
    assert news["sentiment_score"][0] == pytest.approx(0.8)
