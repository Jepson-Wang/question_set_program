"""
在这里面进行短期记忆的开发
主要包括如下功能：
1. 短期记忆的增加和删除
2. 直接获取前n个短期记忆，或者通过元数据进行索引

主要技术分析：
1. 使用队列进行短期记忆的存储
2. 队列的最大长度应该跟我设置的max_length进行比对，如果超过，就应该对队列进行切分
3. 队列的每个元素应该包含元数据，比如时间戳，智能体ID等
4. 应该直接存记忆，不应该有元数据
5. 那么元数据的形式是什么呢？ 直接存字符串就行
6. 需要标注好这是用户传输还是大模型传输，用户传输的话，我需要先对用户传输进行一个优化
7. 存储结构的话，可以用一个字典类型，分为user和model
8. 我还需要为三种获取记忆的方法做一个统一的返回格式

后期可添加功能：
1. 将短期记忆存进redis中
2. 将用户和大模型的对话放入本地缓存中 ， 根据  用户 ID + 会话 ID / 任务 ID + 类型  来存储
"""
import asyncio
from collections import deque
from typing import Dict, List, Any


class MemoryUnit(dict):
    def __init__(self, user_memory : Dict[str,str] ="", model_memory : Dict[str,str] =""):
        super().__init__(
            user = user_memory,
            model = model_memory
        )

#TODO 后续需要完成短期记忆持久化操作
#1. 最近的八条，存入Redis缓存中
#2. 处于队列前端的两条记录，进行提取，并存入RAG或者其他的
class ShortTermMemory:
    def __init__(self, max_memory_size: int = 10):
        # 双端队列存储记忆，max_memory_size 控制最大记忆条数
        #max_memory为9条，多出的那个用来交给相关的大模型进行提炼操作
        self._memory = deque(maxlen=max_memory_size)
        # 协程锁保证并发安全
        self._lock = asyncio.Lock()

    async def add_memory(self, memory: MemoryUnit):
        """添加新记忆（自动删除超出长度的最早记忆）"""
        async with self._lock:
            # memory 示例：{"role": "user", "content": "你好", "timestamp": 1710678900}
            self._memory.append(memory)
            #TODO 后续需要添加判断逻辑：如果当前记忆大于最大记忆，那就将最前面的那个给提取出来

    async def get_latest_memories(self, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最新N条记忆（默认全部）"""
        async with self._lock:
            if limit is None:
                # 返回副本，避免外部修改原队列
                return list(self._memory)
            # 取最后limit条（最新的）
            return list(self._memory)[-limit:]

    async def remove_oldest_memory(self) -> Dict[str, Any] | None:
        """删除最早的1条记忆"""
        #TODO 完成接入大模型进行提取的操作
        async with self._lock:
            if not self._memory:
                return None
            return self._memory.popleft()

    async def clear_all(self):
        """清空所有记忆"""
        async with self._lock:
            self._memory.clear()

    async def get_memory_size(self) -> int:
        """获取当前记忆条数"""
        async with self._lock:
            return len(self._memory)

async def get_short_term_memory() -> ShortTermMemory | None:
    return ShortTermMemory()