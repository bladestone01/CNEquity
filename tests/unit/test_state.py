from datetime import date

from stock_data_engine.storage.state import StateStore


def test_state_store_roundtrip(tmp_path):
    store = StateStore(tmp_path / "meta")
    assert store.get_date("daily_bars") is None
    store.set_date("daily_bars", date(2024, 6, 28))
    assert store.get_date("daily_bars") == date(2024, 6, 28)


def test_state_store_update_max(tmp_path):
    store = StateStore(tmp_path / "meta")
    store.update_max_date("daily_bars", date(2024, 6, 1))
    store.update_max_date("daily_bars", date(2024, 6, 28))
    store.update_max_date("daily_bars", date(2024, 6, 15))
    assert store.get_date("daily_bars") == date(2024, 6, 28)
