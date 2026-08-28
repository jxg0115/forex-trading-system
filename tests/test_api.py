"""API 端到端测试。"""

from datetime import datetime, timedelta, timezone
import uuid

from starlette.testclient import TestClient

from api.app import create_app
from backtest_store.repository import TradeRecord, get_repository
from market_data.service import MarketDataService


class FakeMT5Gateway:
    """测试用假 MT5 网关：只提供行情与账户数据，不触达真实终端。"""

    is_connected = True
    package_available = True

    def get_symbols(self):
        return ["EURUSD", "GBPUSD", "USDJPY"]

    def get_rates(self, symbol="EURUSD", timeframe="M15", count=300):
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}.get(timeframe.upper(), 15)
        price = 1.08
        bars = []
        n = min(count, 500)
        for i in range(n):
            price = price * (1.00005 if i % 7 else 0.99995)
            bars.append(
                {
                    "time": (start - timedelta(minutes=minutes * (n - 1 - i))).isoformat(),
                    "open": round(price, 5),
                    "high": round(price * 1.001, 5),
                    "low": round(price * 0.999, 5),
                    "close": round(price, 5),
                    "volume": 1000.0,
                }
            )
        return bars

    def get_tick(self, symbol="EURUSD"):
        return {"symbol": symbol, "bid": 1.0801, "ask": 1.0802, "last": 1.0801, "volume": 10, "spread_points": 10, "time": 0}

    def get_account_info(self):
        return {"balance": 10000.0, "equity": 10000.0, "profit": 0.0, "margin": 0.0, "free_margin": 10000.0, "leverage": 100, "currency": "USD", "login": 1, "server": "TEST"}

    def get_positions(self):
        return []

    def get_orders(self):
        return []

    def get_closed_trade(self, ticket):
        return None


def _inject_mt5_market(client):
    client.app.state.state.market = MarketDataService(FakeMT5Gateway())


def test_health_and_market():
    with TestClient(create_app()) as client:
        _inject_mt5_market(client)
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        bars = client.get("/api/market/bars", params={"limit": 100})
        assert bars.status_code == 200
        assert len(bars.json()["bars"]) == 100


def test_ai_generate_backtest_save_flow():
    with TestClient(create_app()) as client:
        _inject_mt5_market(client)
        bars = client.get("/api/market/bars", params={"limit": 300}).json()["bars"]
        region = {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "time_start": bars[80]["time"],
            "time_end": bars[130]["time"],
            "price_top": max(b["high"] for b in bars[80:131]),
            "price_bottom": min(b["low"] for b in bars[80:131]),
            "entry_points": [{"time": bars[80]["time"], "price": bars[80]["low"], "label": "入场点"}],
            "exit_points": [{"time": bars[130]["time"], "price": bars[130]["high"], "label": "出场点"}],
        }
        gen = client.post("/api/ai/generate", json={"region": region})
        assert gen.status_code == 200, gen.text
        draft = gen.json()
        assert draft["sandbox"]["ok"] is True

        backtest = client.post(
            "/api/backtest/run",
            json={
                "code": draft["code"],
                "symbol": "EURUSD",
                "timeframe": "M15",
                "bars": 300,
                "params": draft["params"],
            },
        )
        assert backtest.status_code == 200, backtest.text
        assert backtest.json()["ok"] is True

        saved = client.post(
            "/api/factors",
            json={
                "name": draft["name"],
                "description": draft["description"],
                "code": draft["code"],
                "symbol": "EURUSD",
                "timeframe": "M15",
                "source": draft["source"],
                "model": draft["model"],
                "params": draft["params"],
                "tags": draft["tags"],
                "chart_stats": {},
                "prompt_snapshot": {},
                "generated_region": region,
            },
        )
        assert saved.status_code == 201, saved.text
        factor_id = saved.json()["id"]

        factors = client.get("/api/factors")
        assert any(f["id"] == factor_id for f in factors.json()["factors"])


def test_replay_export_with_saved_trade():
    trade_id = f"test-trade-{uuid.uuid4().hex[:10]}"
    repo = get_repository()
    repo.save_trade(
        TradeRecord(
            id=trade_id,
            factor_id="factor-x",
            factor_name="测试因子",
            symbol="EURUSD",
            side="long",
            entry_time=datetime.now(timezone.utc),
            entry_price=1.1000,
            exit_time=datetime.now(timezone.utc),
            exit_price=1.0800,
            lots=0.1,
            pnl=-100.0,
            exit_reason="止损",
            report_markdown="# AI 交易复盘报告",
        )
    )
    repo.close()
    with TestClient(create_app()) as client:
        resp = client.post("/api/replay/export", json={"trade_id": trade_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["score"] == 100.0
        assert body["generated_by"] == "AI 自动复盘"


def test_factor_crud_and_clear():
    with TestClient(create_app()) as client:
        payload = {
            "name": "CRUD 测试因子",
            "description": "增删改查测试",
            "code": "def calculate(df, params):\n    return {'entry': df['close']}\n",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "source": "test",
            "model": "test",
            "params": {},
            "tags": [],
            "chart_stats": {},
            "prompt_snapshot": {},
            "generated_region": {},
        }
        created = client.post("/api/factors", json=payload)
        assert created.status_code == 201
        factor_id = created.json()["id"]

        payload["name"] = "CRUD 已更新"
        updated = client.put(f"/api/factors/{factor_id}", json=payload)
        assert updated.status_code == 200
        assert updated.json()["factor"]["name"] == "CRUD 已更新"

        listed = client.get("/api/factors").json()["factors"]
        assert any(f["id"] == factor_id and f["name"] == "CRUD 已更新" for f in listed)

        deleted = client.delete(f"/api/factors/{factor_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/factors/{factor_id}").status_code == 404

        extra = client.post("/api/factors", json=payload)
        assert extra.status_code == 201
        cleared = client.post("/api/factors/clear")
        assert cleared.status_code == 200
        assert cleared.json()["deleted"] >= 1


def test_matcher_start_with_symbol_timeframe():
    with TestClient(create_app()) as client:
        resp = client.post("/api/matcher/start", json={"symbol": "BTCUSD", "timeframe": "M15"})
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "BTCUSD"
        state = client.get("/api/system/state").json()
        assert state["matcher_symbol"] == "BTCUSD"
        assert state["matcher_timeframe"] == "M15"
        client.post("/api/matcher/stop", json={})


def test_factor_save_requires_sandbox_pass():
    with TestClient(create_app()) as client:
        bad = client.post("/api/factors", json={"name": "危险因子", "description": "测试", "code": "import os\n"})
        assert bad.status_code == 400
        assert "沙盒" in bad.json()["detail"]

        check = client.post("/api/ai/check", json={"code": "def calculate(df, params):\n    return {'entry': df['close']}\n"})
        assert check.status_code == 200
        assert check.json()["ok"] is True


def test_sandbox_diagnose():
    with TestClient(create_app()) as client:
        bad = client.post("/api/ai/sandbox-diagnose", json={"code": "import os\n"})
        assert bad.status_code == 200
        assert bad.json()["summary"] == "静态安全检查未通过"
        assert any("只允许导入" in s for s in bad.json()["suggestions"])

        good = client.post("/api/ai/sandbox-diagnose", json={"code": "def calculate(df, params):\n    return {'entry': df['close']}\n"})
        assert good.status_code == 200
        assert good.json()["summary"] == "沙盒测试通过"
