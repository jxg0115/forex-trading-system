"""在独立子进程中执行因子代码，并做受限内置函数加固。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

from models.factor import FactorExecutionResult

EXEC_TIMEOUT_SECONDS = 15

_SANDBOX_RUNNER = r"""
import json
import sys
import math
import builtins

import numpy as np
import pandas as pd

_ALLOWED_MODULES = {"pandas", "numpy", "math", "statistics"}
_DENIED_IMPORTS = {
    "os", "subprocess", "socket", "pathlib", "shutil", "ctypes", "importlib",
    "multiprocessing", "concurrent", "asyncio", "ftplib", "http", "smtplib", "ssl",
    "telnetlib", "xmlrpc", "zipfile", "tarfile", "fileinput", "tempfile", "glob",
    "pickle", "marshal", "builtins", "gc", "platform", "winreg", "signal", "resource",
    "pty", "mmap", "sqlite3", "shelve", "gzip", "bz2", "lzma", "curses", "dbm",
    "pdb", "pkgutil", "runpy", "site", "tkinter",
}
_STDLIB_NAMES = set(getattr(sys, "stdlib_module_names", ()))
_real_import = builtins.__import__

def _safe_import(name, *args, **kwargs):
    root = name.split(".")[0]
    if root in _DENIED_IMPORTS:
        raise RuntimeError("禁止导入模块：" + name)
    if root in _ALLOWED_MODULES or root in _STDLIB_NAMES or name in sys.modules:
        return _real_import(name, *args, **kwargs)
    else:
        raise RuntimeError("禁止导入模块：" + name)

builtins.__import__ = _safe_import

def _make_deny(name):
    def _deny(*args, **kwargs):
        raise RuntimeError("沙盒禁止内置能力：" + name)
    return _deny

_safe_builtins = {_k: _v for _k, _v in vars(builtins).items()}
for _name in ("open", "eval", "exec", "compile", "input", "breakpoint"):
    _safe_builtins[_name] = _make_deny(_name)

def _to_list(series):
    if series is None:
        return []
    if hasattr(series, "tolist"):
        values = series.tolist()
    else:
        values = list(series)
    return [None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v) for v in values]

def main():
    try:
        payload = json.loads(sys.stdin.read())
        code = payload.get("code", "")
        rows = payload.get("data", [])
        params = payload.get("params", {})
        df = pd.DataFrame(rows)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.set_index("time")
            df["time"] = df.index
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        for _src, _alias in (
            ("open", "Open"),
            ("high", "High"),
            ("low", "Low"),
            ("close", "Close"),
            ("volume", "Volume"),
            ("time", "Time"),
        ):
            if _src in df.columns:
                df[_alias] = df[_src]

        namespace = {
            "__name__": "__factor__",
            "__builtins__": _safe_builtins,
            "pd": pd,
            "np": np,
            "math": math,
            "statistics": __import__("statistics"),
            "params": params,
        }
        exec(compile(code, "<factor>", "exec"), namespace)
        calculate = namespace.get("calculate")
        if calculate is None:
            raise RuntimeError("因子代码未定义 calculate(df, params)")

        output = calculate(df.copy(), params)
        if isinstance(output, dict):
            entry = output.get("entry")
            exit_sig = output.get("exit")
        elif hasattr(output, "dtype"):
            entry = output
            exit_sig = None
        else:
            raise RuntimeError("calculate 必须返回字典或 pandas Series")

        entry_values = _to_list(entry)
        exit_values = _to_list(exit_sig)
        result = {
            "ok": True,
            "entry_values": entry_values,
            "exit_values": exit_values,
            "entry_count": sum(1 for v in entry_values if v not in (None, 0)),
            "exit_count": sum(1 for v in exit_values if v not in (None, 0)),
            "message": "执行成功",
        }
        print(json.dumps(result))
    except Exception as exc:
        result = {
            "ok": False,
            "entry_values": [],
            "exit_values": [],
            "entry_count": 0,
            "exit_count": 0,
            "message": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(result))

if __name__ == "__main__":
    main()
"""


def execute_factor(
    code: str,
    bars: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
    timeout: int = EXEC_TIMEOUT_SECONDS,
) -> FactorExecutionResult:
    """在子进程沙盒中执行因子代码，返回信号序列与统计。"""

    payload = {"code": code, "data": bars, "params": params or {}}
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    start = time.perf_counter()

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SANDBOX_RUNNER],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=os.path.abspath(os.curdir),
        )
    except subprocess.TimeoutExpired:
        return FactorExecutionResult(
            ok=False,
            message=f"因子执行超时（超过 {timeout} 秒）",
            execution_ms=round((time.perf_counter() - start) * 1000, 1),
        )
    except Exception as exc:
        return FactorExecutionResult(
            ok=False,
            message=f"沙盒启动失败：{exc}",
            execution_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    try:
        raw = proc.stdout.strip()
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"ok": False, "message": f"沙盒输出异常：{proc.stdout[:200] or proc.stderr[:200]}"}

    if not data.get("ok") and proc.returncode not in (0, None):
        data["message"] = f"{data.get('message', '')}（进程退出码 {proc.returncode}）"

    return FactorExecutionResult(
        ok=bool(data.get("ok")),
        entry_count=int(data.get("entry_count", 0)),
        exit_count=int(data.get("exit_count", 0)),
        entry_values=[None if v is None else float(v) for v in data.get("entry_values", [])],
        exit_values=[None if v is None else float(v) for v in data.get("exit_values", [])],
        entry_sample=[float(v) for v in data.get("entry_values", [])[-20:] if v is not None],
        exit_sample=[float(v) for v in data.get("exit_values", [])[-20:] if v is not None],
        message=str(data.get("message", "")),
        execution_ms=round((time.perf_counter() - start) * 1000, 1),
    )
