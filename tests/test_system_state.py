"""实盘匹配状态入库与后端重启自动恢复测试。"""

from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from api.app import create_app, restore_saved_matcher_state
from backtest_store.repository import get_repository


def test_system_state_save_and_read_roundtrip():
    repo = get_repository()
    try:
        repo.save_system_state(True, "GOLD", "M30", {"fixed_lots": 0.02, "trailing_enabled": True})
        saved = repo.get_system_state()
        assert saved is not None
        assert saved["matcher_running"] is True
        assert saved["symbol"] == "GOLD"
        assert saved["timeframe"] == "M30"
        assert saved["config"]["fixed_lots"] == 0.02
        assert saved["config"]["trailing_enabled"] is True

        repo.save_system_state(False, "GOLD", "M30", {"fixed_lots": 0.02})
        saved = repo.get_system_state()
        assert saved["matcher_running"] is False
    finally:
        repo.close()


def test_restore_saved_matcher_state_applies_config():
    class FakeExecutor:
        def __init__(self):
            self.config = None

        def apply_runtime_config(self, config):
            self.config = config

    class FakeMatcher:
        def __init__(self):
            self.cfg = {}

        def set_pattern_config(self, **kwargs):
            self.cfg = kwargs

    class FakeRepo:
        def __init__(self):
            self.saved = {
                "matcher_running": True,
                "symbol": "GOLD",
                "timeframe": "M30",
                "config": {
                    "symbol": "GOLD",
                    "timeframe": "M30",
                    "fixed_lots": 0.02,
                    "lot_mode": "fixed",
                    "trailing_enabled": True,
                    "pattern_min_similarity": 0.9,
                    "pattern_min_samples": 2,
                    "pattern_time_decay_days": 30,
                },
            }

        def get_system_state(self):
            return self.saved

    state = SimpleNamespace(
        repository=FakeRepo(),
        signal_executor=FakeExecutor(),
        matcher=FakeMatcher(),
        matcher_symbol="EURUSD",
        matcher_timeframe="M15",
        matcher_pattern_min_similarity=0.85,
        matcher_pattern_min_samples=0,
        matcher_pattern_time_decay_days=365,
    )

    restored = restore_saved_matcher_state(state)
    assert restored is True
    assert state.matcher_running is True
    assert state.matcher_symbol == "GOLD"
    assert state.matcher_timeframe == "M30"
    assert state.signal_executor.config.fixed_lots == 0.02
    assert state.signal_executor.config.lot_mode == "fixed"
    assert state.signal_executor.config.trailing_enabled is True
    assert state.matcher_pattern_min_similarity == 0.9
    assert state.matcher_pattern_min_samples == 2
    assert state.matcher_pattern_time_decay_days == 30
    assert state.matcher.cfg == {
        "min_similarity": 0.9,
        "min_samples": 2,
        "time_decay_days": 30,
    }


def test_restore_saved_matcher_state_skips_when_paused():
    class FakeExecutor:
        def __init__(self):
            self.config = None

        def apply_runtime_config(self, config):
            self.config = config

    class FakeRepo:
        def get_system_state(self):
            return {
                "matcher_running": False,
                "symbol": "GOLD",
                "timeframe": "M30",
                "config": {"trailing_enabled": True, "fixed_lots": 0.05},
            }

    state = SimpleNamespace(
        repository=FakeRepo(),
        signal_executor=FakeExecutor(),
        matcher=SimpleNamespace(set_pattern_config=lambda **kw: None),
        matcher_running=False,
        matcher_symbol="EURUSD",
        matcher_timeframe="M15",
        matcher_pattern_min_similarity=0.85,
        matcher_pattern_min_samples=0,
        matcher_pattern_time_decay_days=365,
    )
    assert restore_saved_matcher_state(state) is False
    assert state.matcher_running is False
    assert state.signal_executor.config.trailing_enabled is True
    assert state.signal_executor.config.fixed_lots == 0.05


def test_runtime_config_saved_without_starting_matcher():
    repo = get_repository()
    try:
        repo.save_system_state(False, "", "", {})
    finally:
        repo.close()

    try:
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/trading/config",
                json={
                    "trailing_enabled": True,
                    "trailing_activation_pct": 0.4,
                    "trailing_retrace_pct": 0.25,
                    "trailing_take_retrace_pct": 0.35,
                    "trailing_take_buffer_pct": 0.15,
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["config"]["trailing_enabled"] is True
            assert body["config"]["trailing_activation_pct"] == 0.4

            loaded = client.get("/api/trading/config").json()
            assert loaded["config"]["trailing_retrace_pct"] == 0.25
            assert loaded["matcher_running"] is False
    finally:
        repo = get_repository()
        try:
            repo.save_system_state(False, "", "", {})
        finally:
            repo.close()


def test_matcher_restore_after_app_restart():
    repo = get_repository()
    try:
        repo.save_system_state(False, "", "", {})
    finally:
        repo.close()

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/matcher/start",
            json={
                "symbol": "GOLD",
                "timeframe": "M30",
                "lot_mode": "fixed",
                "fixed_lots": 0.02,
                "trailing_enabled": True,
                "pattern_min_similarity": 0.9,
                "pattern_min_samples": 2,
                "pattern_time_decay_days": 30,
            },
        )
        assert response.status_code == 200

    with TestClient(create_app()) as client2:
        state = client2.app.state.state
        assert state.matcher_running is True
        assert state.matcher_symbol == "GOLD"
        assert state.matcher_timeframe == "M30"
        assert state.signal_executor.config.fixed_lots == 0.02
        assert state.signal_executor.config.trailing_enabled is True
        assert state.matcher_pattern_min_samples == 2
        client2.post("/api/matcher/stop")
