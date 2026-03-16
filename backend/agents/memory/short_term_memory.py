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
from typing import Dict, List, Any, Optional
from datetime import datetime

from backend.utils.redis_client import get_redis_client

class MemoryUnit(dict):
    def __init__(self, user_memory: Dict[str, str] = "", model_memory: Dict[str, str] = ""):
        super().__init__(
            user=user_memory,
            model=model_memory,
            timestamp=datetime.now().isoformat()
        )


class ShortTermMemory:
    def __init__(self, max_memory_size: int = 10, redis_client=None):
        """
        初始化短期记忆模块
        
        Args:
            max_memory_size: 最大记忆条数
            redis_client: Redis客户端实例，可选，若不传则自动获取全局实例
        """
        self.max_memory_size = max_memory_size
        self._redis_client = redis_client

    def _get_redis_client(self):
        """获取Redis客户端，若未注入则使用全局实例"""
        if self._redis_client is None:
            self._redis_client = get_redis_client()
            return self._redis_client
        return self._redis_client

    async def add_memory(self, user_id: str, session_id: str, memory: MemoryUnit):
        """添加新记忆（自动删除超出长度的最早记忆）"""
        redis_client = self._get_redis_client()
        await redis_client.initialize()
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取现有记忆
        memory_list_str = await redis_client.client.hget(redis_key, "memory_list")
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
        await redis_client.client.hset(
            redis_key,
            mapping={
                "memory_list": json.dumps(memory_list),
                "last_updated": datetime.now().isoformat()
            }
        )
        
        # 设置过期时间（24小时）
        await redis_client.client.expire(redis_key, 86400) #存入redis一天

    async def get_latest_memories(self, user_id: str, session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最新N条记忆"""
        redis_client = self._get_redis_client()
        await redis_client.initialize()
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取记忆列表
        memory_list_str = await redis_client.client.hget(redis_key, "memory_list")
        if not memory_list_str:
            return []
        
        memory_list = json.loads(memory_list_str)
        # 返回最新的limit条
        return memory_list[:limit]

    async def remove_oldest_memory(self, user_id: str, session_id: str) -> Dict[str, Any] | None:
        """删除最早的1条记忆"""
        redis_client = self._get_redis_client()
        await redis_client.initialize()
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取记忆列表
        memory_list_str = await redis_client.client.hget(redis_key, "memory_list")
        if not memory_list_str:
            return None
        
        memory_list = json.loads(memory_list_str)
        if not memory_list:
            return None
        
        # 移除最早的记忆（列表末尾）
        oldest_memory = memory_list.pop()
        
        # 更新Redis
        await redis_client.client.hset(
            redis_key,
            mapping={
                "memory_list": json.dumps(memory_list),
                "last_updated": datetime.now().isoformat()
            }
        )
        
        return oldest_memory

    async def clear_all(self, user_id: str, session_id: str):
        """清空所有记忆"""
        redis_client = self._get_redis_client()
        await redis_client.initialize()
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        # 删除Redis键
        await redis_client.client.delete(redis_key)

    async def get_memory_size(self, user_id: str, session_id: str) -> int:
        """获取当前记忆条数"""
        redis_client = self._get_redis_client()
        await redis_client.initialize()
        
        # 构建Redis键
        redis_key = f"user:{user_id}:session:{session_id}"
        
        # 获取记忆列表
        memory_list_str = await redis_client.client.hget(redis_key, "memory_list")
        if not memory_list_str:
            return 0
        
        memory_list = json.loads(memory_list_str)
        return len(memory_list)


async def get_short_term_memory() -> ShortTermMemory:
    """
    获取短期记忆实例
    
    Returns:
        ShortTermMemory实例
    """
    return ShortTermMemory()