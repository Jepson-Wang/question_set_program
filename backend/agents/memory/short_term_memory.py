"""
在这里面进行短期记忆的开发
主要包括如下功能：
1. 短期记忆的增加和删除
2. 直接获取前n个短期记忆，或者通过元数据进行索引
3. 将短期记忆存储到Redis中，按用户ID和会话ID进行索引

主要技术分析：
1. 使用Redis哈希结构存储记忆，键格式为：user:{user_id}:session:{session_id}
2. 每个会话的记忆使用列表存储，保持最新的记忆在列表头部
3. 使用aioredis进行异步Redis操作
4. 支持设置最大记忆长度，超过后自动删除最早的记忆

存储结构设计：
- Redis键：user:{user_id}:session:{session_id}
- 字段：
  - memory_list: 存储记忆列表的JSON字符串
  - last_updated: 最后更新时间戳

生产环境考虑：
1. Redis连接池管理
2. 错误处理和重试机制
3. 内存使用监控
4. 数据过期策略
5. 备份和恢复机制
"""
import json
from typing import Dict, List, Any
from datetime import datetime

from backend.utils.redis_client import get_redis_client
from redis.exceptions import AuthenticationError

class MemoryUnit(dict):
    def __init__(self, user_memory: str = "", model_memory: str = ""):
        # 注意：这里不要使用 typing.Dict（不可实例化），而要用普通 dict。
        super().__init__(
            memory={
                "user_memory": user_memory,
                "model_memory": model_memory,
            },
            timestamp=datetime.now().isoformat(),
        )


class ShortTermMemory:
    def __init__(self, max_memory_size: int = 10):
        """
        初始化短期记忆模块
        
        Args:
            max_memory_size: 最大记忆条数
            redis_client: Redis客户端实例，可选，若不传则自动获取全局实例
        """
        self.max_memory_size = max_memory_size
        self._redis_client = get_redis_client()

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit):
        """添加新记忆（自动删除超出长度的最早记忆）"""
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取现有记忆
        memory_list_str = await self._redis_client.client.hget(redis_key, "memory_list")
        if memory_list_str:
            memory_list = json.loads(memory_list_str)
        else:
            memory_list = []
        
        # 添加新记忆到列表头部
        memory_list.insert(0, memory)
        
        # 限制记忆长度
        if len(memory_list) > self.max_memory_size:
            memory_list = memory_list[:self.max_memory_size]
        
        # 保存到Redis
        await self._redis_client.client.hset(
            redis_key,
            mapping={
                "memory_list": json.dumps(memory_list),
                "last_updated": datetime.now().isoformat()
            }
        )
        
        # 设置过期时间（24小时）
        await self._redis_client.client.expire(redis_key, 86400) #存入redis一天

    async def get_latest_memories(self, user_id: int, session_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最新N条记忆"""
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取记忆列表
        try:
            memory_list_str = await self._redis_client.client.hget(redis_key, "memory_list")
        except AuthenticationError:
            # Redis 未配置密码/密码错误时，不让整个接口失败（直接返回空记忆）
            return []
        if not memory_list_str:
            return []
        
        memory_list = json.loads(memory_list_str)
        # 返回最新的limit条
        return memory_list[:limit]

    async def remove_oldest_memory(self, user_id: int, session_id: int) -> Dict[str, Any] | None:
        """删除最早的1条记忆"""
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取记忆列表
        memory_list_str = await self._redis_client.client.hget(redis_key, "memory_list")
        if not memory_list_str:
            return None
        
        memory_list = json.loads(memory_list_str)
        if not memory_list:
            return None
        
        # 移除最早的记忆（列表末尾）
        oldest_memory = memory_list.pop()
        
        # 更新Redis
        await self._redis_client.client.hset(
            redis_key,
            mapping={
                "memory_list": json.dumps(memory_list),
                "last_updated": datetime.now().isoformat()
            }
        )
        
        return oldest_memory

    async def clear_all(self, user_id: int, session_id: int):
        """清空所有记忆"""
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        # 删除Redis键
        await self._redis_client.client.delete(redis_key)

    async def get_memory_size(self, user_id: int, session_id: int) -> int:
        """获取当前记忆条数"""
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取记忆列表
        memory_list_str = await self._redis_client.client.hget(redis_key, "memory_list")
        if not memory_list_str:
            return 0
        
        memory_list = json.loads(memory_list_str)
        return len(memory_list)

    async def get_max_memory_size(self) -> int:
        return self.max_memory_size

    async def delete_max_memory(self, user_id: int, session_id: int,size:int):
        """删除超出最大记忆条数的最早记忆"""
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        # 获取记忆列表
        memory_list_str = await self._redis_client.client.hget(redis_key, "memory_list")
        if not memory_list_str:
            return None

        memory_list = json.loads(memory_list_str)
        if len(memory_list) <= size:
            return None

        # 移除最早的记忆（列表末尾）
        for _ in range(size):
            memory_list.pop()

        # 更新Redis
        await self._redis_client.client.hset(
            redis_key,
            mapping={
                "memory_list": json.dumps(memory_list),
                "last_updated": datetime.now().isoformat()
            }
        )

        return memory_list

async def get_short_term_memory() -> ShortTermMemory:
    """
    获取短期记忆实例
    
    Returns:
        ShortTermMemory实例
    """
    return ShortTermMemory()