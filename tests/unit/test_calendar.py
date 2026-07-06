from datetime import date

import polars as pl

from stock_data_engine.adapters.calendar.exchange_calendar import (
    build_trading_calendar,
    calendar_forward_coverage_days,
    calendar_seed_end,
)


def test_cny_holiday_not_trading():
    cal = build_trading_calendar(date(2024, 2, 8), date(2024, 2, 18))
    row = cal.filter(pl.col("trade_date") == date(2024, 2, 12))
    assert row.height == 1
    assert row["is_trading"][0] is False


def test_regular_weekday_is_trading():
    cal = build_trading_calendar(date(2024, 6, 24), date(2024, 6, 28))
    row = cal.filter(pl.col("trade_date") == date(2024, 6, 27))
    assert row["is_trading"][0] is True


def test_weekend_not_trading():
    cal = build_trading_calendar(date(2024, 6, 22), date(2024, 6, 23))
    assert cal.filter(pl.col("is_trading")).height == 0


def test_calendar_forward_coverage_days():
    assert calendar_seed_end() == date(2027, 12, 31)
    assert calendar_forward_coverage_days(date(2027, 10, 2)) == 90
    assert calendar_forward_coverage_days(date(2027, 10, 3)) == 89
    assert calendar_forward_coverage_days(date(2024, 6, 28)) > 90
