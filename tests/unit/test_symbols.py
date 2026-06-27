from stock_data_engine.domain.symbols import format_symbol, is_all_a_symbol, parse_symbol


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
