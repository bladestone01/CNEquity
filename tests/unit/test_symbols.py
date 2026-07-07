from stock_data_engine.domain.symbols import (
    format_symbol,
    is_all_a_symbol,
    is_cdr_symbol,
    parse_symbol,
)


def test_format_symbol():
    assert format_symbol("600519", "SH") == "600519.SH"


def test_parse_symbol():
    info = parse_symbol("000001.SZ")
    assert info.code == "000001"
    assert info.exchange == "SZ"


def test_universe_filter():
    assert is_all_a_symbol("600519", "SH")
    assert is_all_a_symbol("300750", "SZ")
    assert not is_all_a_symbol("810001", "SH")


def test_cdr_symbol():
    assert is_cdr_symbol("689009", "SH")
    assert not is_cdr_symbol("688981", "SH")
    assert not is_cdr_symbol("600519", "SH")
    # CDR segment exists only on SH
    assert not is_cdr_symbol("689009", "SZ")
    # CDRs stay inside the fetch scope; only the all_a universe excludes them
    assert is_all_a_symbol("689009", "SH")
