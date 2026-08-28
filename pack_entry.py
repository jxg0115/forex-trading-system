"""PyInstaller 打包入口：直接启动 FastAPI，不依赖源码路径字符串。"""

from __future__ import annotations

import multiprocessing

import uvicorn

from config import settings
from main import app


if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
