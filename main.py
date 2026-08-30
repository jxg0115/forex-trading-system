"""FastAPI 入口：uvicorn main:app --reload"""

from __future__ import annotations

import json
import socket
import sys
import urllib.request

import uvicorn

from api.app import create_app
from config import settings

app = create_app()


def _port_usable(port: int) -> bool:
    """检查端口是否可绑定；占用、被防火墙/系统保留都会返回 False。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_our_backend(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("app_name") == settings.app_name
    except Exception:
        return False


def resolve_port(preferred: int) -> int:
    """返回可用的启动端口；若本项目已在 preferred 端口运行则直接退出。"""

    if _port_usable(preferred):
        return preferred
    if _is_our_backend(preferred):
        print(f"检测到系统已在 http://127.0.0.1:{preferred} 运行，无需重复启动。")
        sys.exit(0)
    for port in range(preferred + 1, preferred + 21):
        if _port_usable(port):
            print(f"端口 {preferred} 已被其他程序占用，自动切换到端口 {port}。")
            return port
    raise SystemExit(f"端口 {preferred} 至 {preferred + 20} 均不可用，请检查占用后重试。")


if __name__ == "__main__":
    # 关 reload：高频文件变化触发重复重载易崩（稳定性优先；改代码后手动重启）
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=resolve_port(settings.port),
        reload=False,
    )