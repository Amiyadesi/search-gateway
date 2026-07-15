import json
from typing import Any

import redis.asyncio as redis

from app.config import Settings
from app.utils.logging import logger


class CacheService:
    """Redis 缓存包装。Redis 不可用时吞掉异常，让 API 继续工作。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception as exc:
            logger.warning("Redis ping 失败: {}", exc)
            return False

    async def get_json(self, key: str) -> Any | None:
        try:
            value = await self.client.get(key)
            if value is None:
                return None
            return json.loads(value)
        except Exception as exc:
            logger.warning("Redis 读取失败: {}", exc)
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl or self.settings.cache_ttl_seconds)
        except Exception as exc:
            logger.warning("Redis 写入失败: {}", exc)

    async def close(self) -> None:
        await self.client.aclose()
