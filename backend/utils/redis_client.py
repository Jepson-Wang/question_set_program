"""
Redis客户端工具类
负责Redis连接的初始化、管理和关闭
"""
import os
import redis.asyncio as aioredis
from typing import Optional


class RedisClient:
    """Redis客户端管理类"""

    def __init__(self, redis_url: str = "redis://172.18.31.153:6379/0"):
        """
        初始化Redis客户端

        Args:
            redis_url: Redis连接URL
        """
        self.redis_url = redis_url
        self.redis = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )

    async def close(self):
        """关闭Redis连接"""
        if self.redis:
            await self.redis.close()
            self.redis = None

    @property
    def client(self):
        """获取Redis客户端实例"""
        return self.redis


# 全局Redis客户端实例
_redis_client: Optional[RedisClient] = None


def _build_redis_url() -> str:
    """
    兼容配置：
    - 优先使用环境变量 `REDIS_URL`
    - 否则使用 `REDIS_HOST/REDIS_PORT/REDIS_DB/REDIS_USERNAME/REDIS_PASSWORD` 拼接
    """
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url

    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    db = os.getenv("REDIS_DB", "0")
    password = os.getenv("REDIS_PASSWORD")
    username = os.getenv("REDIS_USERNAME")

    # 支持 Redis ACL：redis://username:password@host:port/db
    if password:
        if username:
            return f"redis://{username}:{password}@{host}:{port}/{db}"
        # 无用户名：redis://:password@host:port/db
        return f"redis://:{password}@{host}:{port}/{db}"

    # 无密码但有用户名（较少见）
    if username:
        return f"redis://{username}@{host}:{port}/{db}"

    return f"redis://{host}:{port}/{db}"


def get_redis_client(redis_url: Optional[str] = None) -> RedisClient:
    """
    获取Redis客户端实例

    Args:
        redis_url: Redis连接URL

    Returns:
        RedisClient实例
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient(redis_url or _build_redis_url())
    return _redis_client


async def close_redis():
    """
    关闭全局Redis客户端
    """
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None