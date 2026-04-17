"""
后续还可添加功能
将长期记忆存入RAG知识库中：根据用户输入的问题，整合RAG检索和长短期记忆，返回规划器需要的记忆
"""
import asyncio
from typing import Any

from backend.agents.agent.extract_memory_agent import get_extract_memory
from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.agents.memory.vector_store_manager import VectorStoreManager
from backend.core.single_tool import singleMeta
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


class MemoryManager(metaclass=singleMeta):
    def __init__(self,
                 long_term_memory:LongTermMemory,
                 short_term_memory:ShortTermMemory,
                 vector_memory:VectorStoreManager):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory
        self.vector_memory = vector_memory

    async def get_memory_for_planner(self, user_id: int, session_id: int) -> dict[str, Any]:
        """获取规划器需要的记忆（短期列表 + 单个长期画像）"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        return {
            "short_memory": short_memory,  # list[dict]
            "long_memory": long_memory,    # UserProfileResponse | None
        }

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit):
        """
        对短期记忆进行修改操作，并检查记忆是否已满
        如果已满，则进行记忆的删除，同时将修改后的记忆添加到长期记忆中
        :param user_id:
        :param session_id:
        :param memory:
        :return:
        """

        try:
            # 检查记忆是否已满
            memory_size = await self.short_term_memory.get_memory_size(user_id, session_id)
            max_memory_size = await self.short_term_memory.get_max_memory_size()

            if memory_size < max_memory_size:
                # 未满，直接添加
                await self.short_term_memory.add_memory(user_id, session_id, memory)
            else:
                """
                步骤：
                1. 取出最旧的 1 条记忆（列表尾部）准备归档
                2. 通过大模型提取后存入向量库
                3. 删除最旧的 1 条短期记忆
                4. 将新记忆添加到短期记忆
                """
                import time as _time
                delete_size = 1  # 每次淘汰最旧的 1 条为新记忆腾位
                metadata = {
                    'user_id': user_id,
                    'session_id': session_id,
                    'timestamp': int(_time.time()),
                    'tags': []
                }

                # 1. 获取全量记忆，从尾部取最旧的 delete_size 条
                all_memories = await self.short_term_memory.get_latest_memories(user_id, session_id, memory_size)
                memories_to_archive = all_memories[-delete_size:]

                # 2. 通过大模型提取后存入向量库
                extracted_memory = await get_extract_memory(memories_to_archive)
                for index, memory_dict in enumerate(extracted_memory):
                    metadata['tags'].append(memory_dict['tags'])
                    result = await self.vector_memory.add_document(memory_dict['memory'], metadata)
                    if not result:
                        await asyncio.sleep(0.5)
                        result = await self.vector_memory.add_document(memory_dict['memory'], metadata)
                        if not result:
                            logger.error("添加第%s条记忆到向量数据库失败", index)
                            continue

                # 3. 删除最旧的 delete_size 条短期记忆
                await self.short_term_memory.delete_max_memory(user_id, session_id, delete_size)

                # 4. 添加新记忆
                await self.short_term_memory.add_memory(user_id, session_id, memory)

        except Exception as e:
            logger.error("更新记忆失败：%s", e, exc_info=True)


    #TODO 完成记忆模块的clear功能
