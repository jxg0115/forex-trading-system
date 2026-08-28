"""可选 Redis 特征缓存：未配置 Redis 时自动降级为不缓存。"""

from __future__ import annotations

from typing import Any, Optional

from config import settings


class FeatureCache:
    def __init__(self) -> None:
        self._redis: Any = None
        self.enabled = False
        if settings.redis_url:
            try:
                import redis.asyncio as aioredis  # type: ignore

                self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
                self.enabled = True
            except Exception:
                self._redis = None
                self.enabled = False

    async def get(self, key: str) -> Optional[str]:
        if not self.enabled or self._redis is None:
            return None
        try:
            return await self._redis.get(key)
        except Exception:
            return None

    async def set(self, key: str, value: str, ttl: int = 120) -> None:
        if not self.enabled or self._redis is None:
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except Exception:
            return

