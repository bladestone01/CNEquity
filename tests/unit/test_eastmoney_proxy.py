"""EastMoneyClient proxy wiring."""

from unittest.mock import patch

from stock_data_engine.adapters.eastmoney.em_auth import EastMoneyClient
from stock_data_engine.config import Config


def test_eastmoney_client_passes_config_proxy(tmp_path):
    cfg = Config(data_root=tmp_path / "data", eastmoney_proxy="http://127.0.0.1:7890")
    with patch("stock_data_engine.adapters.eastmoney.em_auth.httpx.Client") as mock_client:
        mock_client.return_value = mock_client
        client = EastMoneyClient(config=cfg)
        client.close()
    kwargs = mock_client.call_args.kwargs
    assert kwargs["proxies"] == "http://127.0.0.1:7890"


def test_eastmoney_client_no_proxy_by_default(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    with patch("stock_data_engine.adapters.eastmoney.em_auth.httpx.Client") as mock_client:
        mock_client.return_value = mock_client
        client = EastMoneyClient(config=cfg)
        client.close()
    assert mock_client.call_args.kwargs.get("proxies") is None
