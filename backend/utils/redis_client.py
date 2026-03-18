"""
Redis客户端工具类
负责Redis连接的初始化、管理和关闭
"""
import aioredis
from typing import Optional


class RedisClient:
    """Redis客户端管理类"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
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


def get_redis_client(redis_url: str = "redis://localhost:6379/0") -> RedisClient:
    """
    获取Redis客户端实例
    
    Args:
        redis_url: Redis连接URL
    
    Returns:
        RedisClient实例
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient(redis_url)
    return _redis_client


async def initialize_redis(redis_url: str = "redis://localhost:6379/0"):
    """
    初始化全局Redis客户端
    
    Args:
        redis_url: Redis连接URL
    """
    client = get_redis_client(redis_url)
    await client.initialize()


async def close_redis():
    """
    关闭全局Redis客户端
    """
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
