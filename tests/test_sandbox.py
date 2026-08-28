"""沙盒执行与隔离测试。"""

from datetime import datetime, timedelta, timezone

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
