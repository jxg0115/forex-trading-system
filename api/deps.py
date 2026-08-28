"""依赖注入：访问应用级服务。"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from backtest_store.repository import FactorRepository, get_repository


def get_app_state(request: Request):
    return request.app.state.state


def repo_dependency() -> Iterator[FactorRepository]:
    """请求级因子库会话，请求结束后自动关闭。"""

    repo = get_repository()
    try:
        yield repo
    finally:
        repo.close()
