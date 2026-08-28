"""AI 管理模块测试：配置 CRUD、板块指派、因子生成接入。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from starlette.testclient import TestClient

from ai_engine.ai_manager import AiManager
from ai_engine.factor_generator import FactorGenerator, TEMPLATES
from api.app import create_app
from backtest_store.repository import get_repository
from models.factor import ChartRegion


def _cleanup(repo):
    for cfg in repo.list_ai_configs():
        repo.delete_ai_config(cfg["id"])
    repo.close()


def test_ai_config_crud_and_masking():
    repo = get_repository()
    try:
        cfg = repo.create_ai_config(
            name="主力-千问",
            provider="openai",
            model="qwen3.8-max",
            api_key="sk-test-1234567890",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            roles=["factor_learning", "trade_replay"],
            enabled=True,
        )
        assert cfg["name"] == "主力-千问"
        assert cfg["api_key_masked"] == "sk-t****7890"
        assert "factor_learning" in cfg["roles"]

        updated = repo.update_ai_config(cfg["id"], name="主力-千问V2", roles=["market_monitor"])
        assert updated["name"] == "主力-千问V2"
        assert updated["roles"] == ["market_monitor"]
        assert repo.delete_ai_config(cfg["id"]) is True
    finally:
        _cleanup(repo)


def test_ai_manager_active_for_role():
    repo = get_repository()
    try:
        repo.create_ai_config(
            name="复盘AI",
            provider="openai",
            model="qwen3.8-max",
            api_key="sk-test-123",
            base_url="",
            roles=["trade_replay"],
            enabled=True,
        )
        manager = AiManager(repo)
        assert manager.active_for("factor_learning") is None
        replay_cfg = manager.active_for("trade_replay")
        assert replay_cfg is not None
        assert replay_cfg["api_key"] == "sk-test-123"
    finally:
        _cleanup(repo)


def test_ai_test_connection_requires_key():
    repo = get_repository()
    try:
        cfg = repo.create_ai_config(
            name="无KeyAI",
            provider="openai",
            model="qwen3.8-max",
            api_key="",
            base_url="",
            roles=["factor_learning"],
            enabled=True,
        )
        manager = AiManager(repo)
        result = asyncio.run(manager.test_connection(cfg["id"]))
        assert result["success"] is False
        assert "API Key" in result["message"]
    finally:
        _cleanup(repo)


class FakeAiManager:
    def __init__(self):
        self.calls = 0

    def active_for(self, role):
        return {"id": "cfg-1", "model": "qwen3.8-max"} if role == "factor_learning" else None

    async def generate(self, config_id, prompt, chart_image_data_url=None, system_prompt=""):
        self.calls += 1
        return TEMPLATES["volatility"][0]


def test_factor_generator_uses_ai_manager():
    manager = FakeAiManager()
    generator = FactorGenerator(ai_manager=manager)
    region = ChartRegion(
        symbol="GOLD",
        timeframe="M15",
        time_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        time_end=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
        price_top=100.0,
        price_bottom=90.0,
    )
    bars = []
    price = 95.0
    for i in range(40):
        price *= 1.001 if i % 2 else 0.999
        bars.append(
            {
                "time": f"2026-08-01T00:{i:02d}:00Z",
                "open": round(price, 2),
                "high": round(price * 1.002, 2),
                "low": round(price * 0.998, 2),
                "close": round(price, 2),
                "volume": 1000.0,
            }
        )

    draft = asyncio.run(generator.generate(region, bars))
    assert manager.calls == 1
    assert draft.model == "qwen3.8-max"
    assert draft.source == "ai"


def test_ai_config_api():
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/ai/configs",
            json={
                "name": "测试千问",
                "provider": "openai",
                "model": "qwen3.8-max",
                "api_key": "sk-test-1234567890",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "roles": ["factor_learning"],
                "enabled": True,
            },
        )
        assert response.status_code == 201
        cfg_id = response.json()["config"]["id"]

        status = client.get("/api/ai/status").json()
        assert status["configured"] is True
        assert "测试千问" in status["roles"]["factor_learning"]["configs"]

        listed = client.get("/api/ai/configs").json()["configs"]
        assert listed[0]["api_key_masked"] == "sk-t****7890"

        updated = client.put(
            f"/api/ai/configs/{cfg_id}",
            json={
                "name": "测试千问V2",
                "provider": "openai",
                "model": "qwen3.8-max",
                "api_key": "",
                "base_url": "",
                "roles": ["market_monitor"],
                "enabled": False,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["config"]["name"] == "测试千问V2"
        assert updated.json()["config"]["enabled"] is False

        deleted = client.delete(f"/api/ai/configs/{cfg_id}")
        assert deleted.status_code == 200
