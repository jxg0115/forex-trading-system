"""MT5 网关合并相关测试。"""

from starlette.testclient import TestClient

from api.app import create_app
from oems.mt5_gateway import MT5Gateway


def test_mt5_router_returns_paper_mode_hint():
    with TestClient(create_app()) as client:
        resp = client.get("/api/mt5/status")
        assert resp.status_code == 400
        assert "模拟经纪商模式" in resp.json()["detail"]


def test_mt5_gateway_degrades_without_package(monkeypatch):
    """未安装 MetaTrader5 时，网关不能崩溃，接口返回空数据。"""

    def fake_require(self):
        raise RuntimeError("未安装 MetaTrader5 Python 包，无法连接 MT5")

    monkeypatch.setattr(MT5Gateway, "_require_mt5", fake_require)
    gateway = MT5Gateway()
    assert gateway.connect() is False
    assert gateway.get_rates("EURUSD", "M15", 100) == []
    assert gateway.get_positions() == []
