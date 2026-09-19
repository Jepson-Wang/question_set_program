"""
短期会话记忆：Redis LIST 实现。

为什么是 LIST 而不是「hash 字段里的 JSON 字符串」：
旧实现每次写入都要「读出整个列表 -> Python 里 insert -> 整个写回」，
是一次没有任何保护的 read-modify-write，两个并发请求会丢消息（读-读-写-写）。
改成 LIST 后，写入是 Redis 服务端的原子操作。

淘汰同样在服务端完成：LPUSH 与「超限时 RPOPLPUSH 到 pending 队列」
打包进一次 Lua EVAL 原子执行。被淘汰的条目在这一刻就已经物理移出窗口，
后续的归档读不到它、也不会被新消息插入干扰，所以整条链路不需要任何锁。

存储结构：
- stm:{user_id}:{session_id}          LIST，index 0 为最新，长度上限 max_memory_size
- stm:pending:{user_id}:{session_id}  LIST，待归档队列，归档成功后 LREM 移除
"""
import json
from typing import Dict, List, Any, Optional
from datetime import datetime

from backend.middleware.logging import get_logger
from backend.utils.redis_client import get_redis_client
from redis.exceptions import RedisError

logger = get_logger(__name__)

DEFAULT_TTL = 86400
DEFAULT_PENDING_TTL = 604800

_PUSH_AND_EVICT_LUA = """
redis.call('LPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
local evicted = {}
while redis.call('LLEN', KEYS[1]) > tonumber(ARGV[2]) do
  local item = redis.call('RPOPLPUSH', KEYS[1], KEYS[2])
  if not item then break end
  table.insert(evicted, item)
end
if #evicted > 0 then
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
end
return evicted
"""

_short_term_memory: Optional[ShortTermMemory] = None


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
    def __init__(
            self,
            max_memory_size: int = 10,
            ttl: int = DEFAULT_TTL,
            pending_ttl = DEFAULT_PENDING_TTL
    ):
        """
        初始化短期记忆模块
        
        Args:
            max_memory_size: 最大记忆条数
            redis_client: Redis客户端实例，可选，若不传则自动获取全局实例
        """
        self.max_memory_size = max_memory_size
        self._client = get_redis_client().client
        self.ttl = ttl
        self.pending_ttl = pending_ttl
        self._push_script = None

    @staticmethod
    def key(user_id: int,session_id: int) -> str:
        return f"stm:{user_id}:{session_id}"

    @staticmethod
    def pending_key(user_id: int,session_id: int) -> str:
        return f"stm:pending:{user_id}:{session_id}"

    @staticmethod
    def parse_pending_key(key: str) -> tuple[int,int]:
        parts = key.split(":")
        return int(parts[2]),int(parts[3])

    def _script(self):
        if self._push_script is None:
            self._push_script = self._client.register_script(_PUSH_AND_EVICT_LUA)
        return self._push_script

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit) -> List[str]:
        """
        原子写入一条记忆，并把超过窗口容量的最旧记忆搬进pending队列

        :return: 本次被挤出窗口的原始JSON字符串列表，调用方将他们归档
        """
        try:
            evicted = await self._script()(
                keys = [
                    self.key(user_id,session_id),
                    self.pending_key(user_id,session_id)
                ],
                args = [
                    json.dumps(memory,ensure_ascii=False),
                    self.max_memory_size,
                    self.ttl,
                    self.pending_ttl
                ],
            )
            return list(evicted or [])
        except RedisError as e:
            logger.error("写入短期记忆失败: %s",e,exc_info=True)
            return []
        

    async def ack_archived(self,user_id: int,session_id: int,raw_item: str) -> int:
        """
        归档成功后，把该条目从pending队列中删除
        :return: 实际移除的条数
        """
        try:
            return await self._client.lrem(
                self.pending_key(user_id,session_id),1,raw_item
            )
        except RedisError as e:
            logger.error("移除pending条目失败: %s",e,exc_info=True)
            return 0


    async def clear_all(self,user_id: int,session_id: int) -> None:
        """清空会话窗口和pending队列"""
        await self._client.delete(
            self.key(user_id,session_id),
            self.pending_key(user_id,session_id),
        )

    async def get_latest_memories(self, user_id: int, session_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最新N条记忆"""
        try:
            raw = await self._client.lrange(
                self.key(user_id,session_id),0,limit-1
            )
        except RedisError as e:
            logger.error("读取短期记忆失败: %s",e)
            return []

        result: List[Dict[str,Any]] = []
        for item in raw:
            try:
                result.append(json.loads(item))
            except json.JSONDecodeError:
                logger.error("短期记忆反序列化失败，已跳过: %s",item[:100])
        return result

    async def get_memory_size(self, user_id: int, session_id: int) -> int:
        try:
            return await self._client.llen(self.key(user_id,session_id))
        except RedisError:
            return 0

    async def get_pending(self,user_id: int,session_id: int) -> List[str]:
        try:
            return await self._client.lrange(
                self.pending_key(user_id,session_id),0,-1
            )
        except RedisError:
            return []

    async def scan_pending_keys(self) -> List[str]:
        keys:List[str] = []
        try:
            async for key in self._client.scan_iter(match="stm:pending:*",count = 100):
                keys.append(key)
        except RedisError as e:
            logger.error("扫描 pending 队列失败: %s", e)
        return keys


async def get_short_term_memory() -> ShortTermMemory:
    """
    获取短期记忆实例
    
    Returns: ShortTermMemory实例
    """
    global _short_term_memory
    if _short_term_memory is None:
        _short_term_memory = ShortTermMemory()
    return _short_term_memory #type: ignore