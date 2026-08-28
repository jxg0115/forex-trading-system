"""AST 静态安全检查测试。"""

from sandbox.ast_check import check_source

VALID_CODE = """\
import pandas as pd
import numpy as np

def calculate(df, params):
    close = df["close"]
    entry = pd.Series(0.0, index=df.index)
    entry.loc[close > close.mean()] = 1.0
    return {"entry": entry}
"""


def test_valid_code_passes():
    result = check_source(VALID_CODE)
    assert result.ok
    assert not result.errors


def test_forbidden_import_rejected():
    code = 'import os\n\ndef calculate(df, params):\n    return {"entry": df["close"]}\n'
    result = check_source(code)
    assert not result.ok
    assert any("禁止导入" in e for e in result.errors)


def test_dangerous_builtin_rejected():
    code = 'def calculate(df, params):\n    open("x")\n    return {"entry": df["close"]}\n'
    result = check_source(code)
    assert not result.ok
    assert any("open" in e for e in result.errors)


def test_while_loop_rejected():
    code = 'def calculate(df, params):\n    while True:\n        pass\n    return {"entry": df["close"]}\n'
    result = check_source(code)
    assert not result.ok
    assert any("while" in e for e in result.errors)


def test_magic_attribute_rejected():
    code = 'def calculate(df, params):\n    x = df.__class__\n    return {"entry": df["close"]}\n'
    result = check_source(code)
    assert not result.ok
    assert any("魔法属性" in e for e in result.errors)


def test_missing_calculate_rejected():
    code = 'import pandas as pd\nx = 1\n'
    result = check_source(code)
    assert not result.ok
    assert any("calculate" in e for e in result.errors)

