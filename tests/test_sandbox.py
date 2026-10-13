"""沙盒执行与隔离测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from sandbox import runner
from sandbox.ast_check import check_source
from sandbox.runner import execute_factor

CODE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    lookback = int(params.get("lookback", 20))
    close = df["close"]
    high = df["high"].rolling(lookback).max().shift(1)
    entry = pd.Series(0.0, index=df.index)
    entry.loc[close > high] = 1.0
    return {"entry": entry}
"""


def _bars(n: int = 200) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = 1.08
    bars: list[dict] = []
    for i in range(n):
        price *= 1.005
        bars.append(
            {
                "time": (start + timedelta(minutes=15 * i)).isoformat(),
                "open": round(price, 5),
                "high": round(price * 1.002, 5),
                "low": round(price * 0.998, 5),
                "close": round(price, 5),
                "volume": 1000.0,
            }
        )
    return bars


def test_execute_valid_factor():
    result = execute_factor(CODE, _bars())
    assert result.ok
    assert result.message == "执行成功"
    assert result.entry_count > 0
    assert len(result.entry_values) == 200


def test_runtime_blocks_dangerous_builtin():
    code = (
        'def calculate(df, params):\n'
        '    open("C:/windows/win.ini")\n'
        '    return {"entry": df["close"]}\n'
    )
    result = execute_factor(code, _bars())
    assert not result.ok
    assert "open" in result.message


def test_runtime_blocks_dangerous_import():
    code = (
        'import subprocess\n'
        'def calculate(df, params):\n'
        '    return {"entry": df["close"]}\n'
    )
    result = execute_factor(code, _bars())
    assert not result.ok
    assert "禁止导入" in result.message


def test_check_and_run_consistency():
    assert check_source(CODE).ok
    assert execute_factor(CODE, _bars()).ok


# ---------------------------------------------------------------------------
# 进程复用 + 缓存 + 无窗口（2026-10-13 治本优化）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sandbox_state():
    runner._reset_sandbox_state()
    runner._stats.update(cache_hits=0, worker_runs=0, oneshot_runs=0, worker_spawns=0, worker_kills=0)
    yield
    runner._reset_sandbox_state()


def test_second_call_same_data_hits_cache_no_new_process():
    execute_factor(CODE, _bars())
    spawns_after_first = runner._stats["worker_spawns"]
    r2 = execute_factor(CODE, _bars())
    assert r2.ok
    assert runner._stats["cache_hits"] == 1
    assert runner._stats["worker_spawns"] == spawns_after_first


def test_cache_invalidates_when_new_bar_arrives():
    bars = _bars()
    execute_factor(CODE, bars)
    bars.append(
        {
            "time": (bars[-1]["time"]),
            "open": 1.10,
            "high": 1.11,
            "low": 1.09,
            "close": 1.10,
            "volume": 1000.0,
        }
    )
    execute_factor(CODE, bars)
    assert runner._stats["cache_hits"] == 0
    assert runner._stats["worker_runs"] == 2


def test_cache_invalidates_when_params_change():
    bars = _bars()
    execute_factor(CODE, bars, {"lookback": 10})
    execute_factor(CODE, bars, {"lookback": 30})
    assert runner._stats["cache_hits"] == 0
    assert runner._stats["worker_runs"] == 2


def test_worker_is_reused_across_different_factors():
    execute_factor(CODE, _bars())
    execute_factor(CODE, _bars(150))  # 同 code 不同数据 = 第二个任务
    assert runner._stats["worker_spawns"] == 1
    assert runner._stats["worker_runs"] == 2


def test_worker_respawns_after_being_killed():
    execute_factor(CODE, _bars())
    runner._kill_worker()
    r = execute_factor(CODE, _bars(120))
    assert r.ok
    assert runner._stats["worker_spawns"] == 2


def test_all_subprocesses_created_without_window(monkeypatch):
    seen_flags: list[int] = []

    real_popen = runner.subprocess.Popen

    def _spy_popen(*args, **kwargs):
        seen_flags.append(kwargs.get("creationflags", 0))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", _spy_popen)
    execute_factor(CODE, _bars())
    assert seen_flags, "Popen 未被调用"
    assert all(f & runner.CREATE_NO_WINDOW for f in seen_flags if runner.CREATE_NO_WINDOW)


def test_worker_path_result_matches_oneshot_path():
    """工人路径与一次性兜底路径结果必须逐值一致（防线等价性）。"""
    bars = _bars()
    r_worker = execute_factor(CODE, bars)
    runner._reset_sandbox_state()
    runner._stats.update(worker_runs=0, oneshot_runs=0)
    # 强制走一次性路径：临时把 _run_in_worker 打回 None
    original = runner._run_in_worker
    runner._run_in_worker = lambda payload, timeout: None
    try:
        r_oneshot = execute_factor(CODE, bars)
    finally:
        runner._run_in_worker = original
    assert runner._stats["oneshot_runs"] == 1
    assert r_worker.ok and r_oneshot.ok
    assert r_worker.entry_values == r_oneshot.entry_values
    assert r_worker.entry_count == r_oneshot.entry_count


def test_worker_blocks_dangerous_import_like_oneshot():
    code = (
        'import subprocess\n'
        'def calculate(df, params):\n'
        '    return {"entry": df["close"]}\n'
    )
    result = execute_factor(code, _bars())
    assert not result.ok
    assert "禁止导入" in result.message
    # 工人仍存活（防线在脚本内，不杀进程）
    assert runner._worker is not None and runner._worker.poll() is None


def test_factor_print_does_not_break_protocol():
    code = (
        'def calculate(df, params):\n'
        '    print("noise " * 100)\n'
        '    return {"entry": df["close"]}\n'
    )
    result = execute_factor(code, _bars())
    assert result.ok
    assert result.entry_count > 0
