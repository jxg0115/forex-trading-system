# -*- coding: utf-8 -*-
"""MT5 策略测试器桥接控制（系统正式功能）：启动/停止/状态/功能勾选。

- GET  /api/bridge/status  -> 桥实时状态（运行/品种/周期/进度/功能参与/笔数/累计）
- POST /api/bridge/start   -> 启动桥（后台常驻；已在运行则返回现有状态）
- POST /api/bridge/stop    -> 停止桥
- GET  /api/bridge/config  -> 功能参与勾选（读 bridge_settings.json，无文件用默认）
- POST /api/bridge/config  -> 保存功能参与勾选（桥实时生效：下个决策循环读取）

桥目录（与 EA 共用）：Common\\Files\\dsb（Tester 沙箱唯一可写路径）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from fastapi import APIRouter

router = APIRouter(prefix="/api/bridge", tags=["MT5 测试器桥"])

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 主线根
BRIDGE_DIR = os.path.join(os.environ.get("APPDATA", r"C:\Users\xg\AppData\Roaming"),
                          "MetaQuotes", "Terminal", "Common", "Files", "dsb")
STATUS_FILE = os.path.join(BRIDGE_DIR, "bridge_status.json")
SETTINGS_FILE = os.path.join(BRIDGE_DIR, "bridge_settings.json")
BRIDGE_PY = os.path.join(_HERE, "tools", "ea_bridge.py")
PY = os.path.join(_HERE, "runtime", "python-embed", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

DEFAULT_SETTINGS = {
    "factors": True, "sltp": True, "smart_stop": True,
    "events": True, "risk": True, "patterns": False, "ai": False,
}
FRESH_S = 30  # 状态文件超过 30s 未更新视为桥离线


def _read_status() -> dict:
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: bool(data.get(k, v)) for k, v in DEFAULT_SETTINGS.items()}
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)


@router.get("/status")
def status() -> dict:
    st = _read_status()
    running = False
    if st.get("ts") and time.time() - st["ts"] < FRESH_S:
        running = True
    return {
        "running": running,
        "status": st,
        "settings": _read_settings(),
        "bridge_dir": BRIDGE_DIR,
        "fresh_s": FRESH_S,
    }


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


@router.post("/start")
def start() -> dict:
    st = _read_status()
    if (st.get("ts") and time.time() - st["ts"] < FRESH_S
            and _pid_alive(st.get("pid"))):
        return {"ok": True, "already_running": True, "status": st}
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    try:
        proc = subprocess.Popen(
            [PY, "-u", BRIDGE_PY],
            cwd=_HERE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "pid": proc.pid, "already_running": False}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/stop")
def stop() -> dict:
    st = _read_status()
    pid = st.get("pid")
    if pid and _pid_alive(pid):
        try:
            import signal
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(int(pid), signal.SIGKILL if hasattr(signal, "SIGKILL") else 9)
            except Exception:
                pass
    # 清状态文件（避免 start 误判已运行）
    try:
        os.remove(STATUS_FILE)
    except Exception:
        pass
    return {"ok": True, "stopped_pid": pid if _pid_alive(pid) else None}


@router.get("/config")
def get_config() -> dict:
    return {"settings": _read_settings(), "defaults": DEFAULT_SETTINGS}


@router.post("/config")
def set_config(payload: dict) -> dict:
    data = {k: bool(payload.get(k, v)) for k, v in DEFAULT_SETTINGS.items()}
    try:
        os.makedirs(BRIDGE_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"ok": True, "settings": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}