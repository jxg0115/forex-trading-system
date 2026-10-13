"""在独立子进程中执行因子代码，并做受限内置函数加固。

性能设计（两层加速，防线不变）：
1. 结果缓存：以 (code, bars 指纹, params) 为键，同一份数据不重复计算
   （M15 K 线 15 分钟才出一根，扫描循环 15s 一轮，99% 以上的调用是重复数据）。
2. 常驻沙盒工人：进程只启动一次，父进程通过 stdin/stdout 行协议（一行一 JSON）
   反复派活，省掉每次 ~500ms 的进程创建 + numpy/pandas 导入开销。
   安全防线与一次性模式完全一致（import 钩子、内置函数加固、AST 预检、超时）。
3. 所有子进程创建均带 CREATE_NO_WINDOW，杜绝控制台窗口闪烁。

工人不可用（起不来/读写失败/超时）时自动回退一次性子进程路径，
并杀掉问题工人等下一次重建；功能与旧版逐值一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from models.factor import FactorExecutionResult

EXEC_TIMEOUT_SECONDS = 15

# Windows 专有：抑制子进程控制台窗口（0 在非 Windows 平台上即无操作）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 因子 print 的输出在工人模式下会污染协议，截断到 64KB 后丢弃
_WORKER_PRINT_CAP = 64 * 1024

# ---------------------------------------------------------------------------
# 一次性执行脚本（兜底路径用，保持旧版行为不变）
# ---------------------------------------------------------------------------

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
    # pandas/numpy 内部惰性依赖 concurrent 包（先 import 裸包再取 .futures，线程池）；
    # 因子显式 import concurrent* 仍由 AST 层拒绝（root 不在 ALLOWED_IMPORTS）
    if root == "concurrent":
        return _real_import(name, *args, **kwargs)
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

# ---------------------------------------------------------------------------
# 常驻工人脚本：与一次性脚本同一套防线，改为循环收发一行一 JSON。
# 要点：因子代码的 print 必须重定向（否则污染协议行）；协议只用真实 stdout。
# ---------------------------------------------------------------------------

_SANDBOX_WORKER = r"""
import io
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
_REAL_STDOUT = sys.stdout

def _safe_import(name, *args, **kwargs):
    root = name.split(".")[0]
    if root == "concurrent":
        return _real_import(name, *args, **kwargs)
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

def _handle(payload):
    try:
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
        # 因子 print 重定向到内存缓冲（截断），避免污染 stdout 协议行
        _buf = io.StringIO()
        _old = sys.stdout
        sys.stdout = _buf
        try:
            exec(compile(code, "<factor>", "exec"), namespace)
            calculate = namespace.get("calculate")
            if calculate is None:
                raise RuntimeError("因子代码未定义 calculate(df, params)")
            output = calculate(df.copy(), params)
        finally:
            sys.stdout = _old
            if _buf.tell() > 65536:
                _buf.seek(0)
                _buf.truncate()

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
        return {
            "ok": True,
            "entry_values": entry_values,
            "exit_values": exit_values,
            "entry_count": sum(1 for v in entry_values if v not in (None, 0)),
            "exit_count": sum(1 for v in exit_values if v not in (None, 0)),
            "message": "执行成功",
        }
    except Exception as exc:
        return {
            "ok": False,
            "entry_values": [],
            "exit_values": [],
            "entry_count": 0,
            "exit_count": 0,
            "message": f"{type(exc).__name__}: {exc}",
        }

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            payload = json.loads(line)
        except Exception as exc:
            out = {"ok": False, "entry_values": [], "exit_values": [],
                   "entry_count": 0, "exit_count": 0,
                   "message": f"协议错误: {type(exc).__name__}: {exc}"}
        else:
            out = _handle(payload)
        _REAL_STDOUT.write(json.dumps(out) + "\n")
        _REAL_STDOUT.flush()

if __name__ == "__main__":
    main()
"""

# ---------------------------------------------------------------------------
# 结果缓存 + 常驻工人管理
# ---------------------------------------------------------------------------

_CACHE: dict[str, FactorExecutionResult] = {}
_CACHE_MAX = 512

_worker: subprocess.Popen | None = None
_lock = threading.Lock()  # 串行化派活，保护 _worker 与管道读写

# 运行统计（测试与线上观测用）
_stats = {"cache_hits": 0, "worker_runs": 0, "oneshot_runs": 0, "worker_spawns": 0, "worker_kills": 0}


def _fingerprint(code: str, bars: list[dict[str, Any]], params: dict[str, Any]) -> str:
    """数据指纹：K 线根数 + 首尾各若干根的 (time, close) + code + params。

    只取首尾样本而非全量序列：matcher 每轮传入的 bars 是同一起点的滚动窗口，
    未收盘时整段不变；新 K 线出现时尾样必变，指纹随之失效。
    """
    n = len(bars)
    tail = bars[-3:] if n >= 3 else bars
    head = bars[:1]
    raw = json.dumps(
        {
            "code": code,
            "params": params or {},
            "n": n,
            "head": [[b.get("time"), b.get("close")] for b in head],
            "tail": [[b.get("time"), b.get("close")] for b in tail],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _spawn_worker() -> subprocess.Popen | None:
    """启动（或重建）常驻沙盒工人；失败返回 None（调用方走兜底）。"""
    global _worker
    try:
        _worker = subprocess.Popen(
            [sys.executable, "-c", _SANDBOX_WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=_child_env(),
            cwd=os.path.abspath(os.curdir),
            creationflags=CREATE_NO_WINDOW,
        )
        _stats["worker_spawns"] += 1
    except Exception:
        _worker = None
    return _worker


def _kill_worker() -> None:
    global _worker
    proc, _worker = _worker, None
    if proc is None:
        return
    _stats["worker_kills"] += 1
    try:
        proc.kill()
    except Exception:
        pass


def _ensure_worker() -> subprocess.Popen | None:
    if _worker is not None and _worker.poll() is None:
        return _worker
    return _spawn_worker()


def _write_line(proc: subprocess.Popen, line: str, timeout: float) -> None:
    """带超时地写一行（worker 卡死不读时避免父进程无限阻塞）。"""
    q: queue.Queue = queue.Queue()

    def _w() -> None:
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
            q.put(None)
        except Exception as exc:  # pragma: no cover - 管道断裂等
            q.put(exc)

    threading.Thread(target=_w, daemon=True).start()
    err = q.get(timeout=timeout)
    if err is not None:
        raise err


def _read_line(proc: subprocess.Popen, timeout: float) -> str:
    """带超时地读一行；超时/EOF 抛异常，由调用方杀掉工人重建。"""
    q: queue.Queue = queue.Queue()

    def _r() -> None:
        try:
            q.put(proc.stdout.readline())
        except Exception as exc:  # pragma: no cover
            q.put(exc)

    threading.Thread(target=_r, daemon=True).start()
    line = q.get(timeout=timeout)
    if isinstance(line, Exception):
        raise line
    if not line:
        raise EOFError("沙盒工人已退出")
    return line


def _run_in_worker(payload: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    """派活给常驻工人；任何异常都杀工人并返回 None（触发兜底）。"""
    with _lock:
        worker = _ensure_worker()
        if worker is None:
            return None
        try:
            _write_line(worker, json.dumps(payload, default=str), timeout)
            line = _read_line(worker, timeout)
            data = json.loads(line)
            _stats["worker_runs"] += 1
            return data
        except Exception:
            _kill_worker()
            return None


def _run_oneshot(payload: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    """一次性子进程兜底（旧版路径 + CREATE_NO_WINDOW）。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SANDBOX_RUNNER],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(),
            cwd=os.path.abspath(os.curdir),
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "entry_values": [],
            "exit_values": [],
            "entry_count": 0,
            "exit_count": 0,
            "message": f"因子执行超时（超过 {timeout} 秒）",
        }
    except Exception as exc:
        return {
            "ok": False,
            "entry_values": [],
            "exit_values": [],
            "entry_count": 0,
            "exit_count": 0,
            "message": f"沙盒启动失败：{exc}",
        }
    _stats["oneshot_runs"] += 1
    raw = proc.stdout.strip()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"ok": False, "message": f"沙盒输出异常：{proc.stdout[:200] or proc.stderr[:200]}"}
    if not data.get("ok") and proc.returncode not in (0, None):
        data["message"] = f"{data.get('message', '')}（进程退出码 {proc.returncode}）"
    return data


def _build_result(data: dict[str, Any], start: float) -> FactorExecutionResult:
    entry_values = [v if v is None else float(v) for v in data.get("entry_values", [])]
    exit_values = [v if v is None else float(v) for v in data.get("exit_values", [])]
    return FactorExecutionResult(
        ok=bool(data.get("ok")),
        entry_count=int(data.get("entry_count", 0)),
        exit_count=int(data.get("exit_count", 0)),
        entry_values=entry_values,
        exit_values=exit_values,
        entry_sample=[v for v in entry_values[-20:] if v is not None],
        exit_sample=[v for v in exit_values[-20:] if v is not None],
        message=str(data.get("message", "")),
        execution_ms=round((time.perf_counter() - start) * 1000, 1),
    )


def execute_factor(
    code: str,
    bars: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
    timeout: int = EXEC_TIMEOUT_SECONDS,
) -> FactorExecutionResult:
    """在子进程沙盒中执行因子代码，返回信号序列与统计。

    同一份 (code, bars, params) 直接命中缓存；未命中走常驻工人，
    工人不可用时回退一次性子进程。函数签名与返回结构与旧版一致。
    """
    params = params or {}
    key = _fingerprint(code, bars, params)
    cached = _CACHE.get(key)
    if cached is not None:
        _stats["cache_hits"] += 1
        return cached

    payload = {"code": code, "data": bars, "params": params}
    start = time.perf_counter()

    data = _run_in_worker(payload, timeout)
    if data is None:
        data = _run_oneshot(payload, timeout)
    if data is None:  # pragma: no cover - 双路径均失败的极端情况
        data = {
            "ok": False,
            "entry_values": [],
            "exit_values": [],
            "entry_count": 0,
            "exit_count": 0,
            "message": "沙盒不可用",
        }

    result = _build_result(data, start)
    if result.ok:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = result
    return result


def _reset_sandbox_state() -> None:
    """清空缓存并杀掉工人（测试用；线上不调用）。"""
    with _lock:
        _CACHE.clear()
        _kill_worker()
