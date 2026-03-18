"""
后续还可添加功能
将长期记忆存入RAG知识库中：根据用户输入的问题，整合RAG检索和长短期记忆，返回规划器需要的记忆
"""

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit


class MemoryManager:
    def __init__(self,
                 long_term_memory:LongTermMemory,
                 short_term_memory:ShortTermMemory):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory

    async def get_memory_for_planner(self, user_id: int, session_id: str):
        """获取规划器需要的记忆（短期+长期）"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        return {
            "short_memory": short_memory,
            "long_memory": long_memory
        }

    async def update_memory(self, user_id: int, session_id: str, memory: MemoryUnit):
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
                # 添加到短期记忆
                await self.short_term_memory.add_memory(user_id, session_id, memory)
            else:
                """
                步骤：
                1.调用方法get_latest_memories获取超过max_size的那几个记忆
                2.将这几个记忆先通过大模型进行提取，并添加到长期记忆中
                3.将这段记忆切分后返回给RAG知识库（还未开发）
                4.然后进行短期记忆的删除操作
                """
                memories = await self.short_term_memory.get_latest_memories(user_id, session_id,memory_size)[-(memory_size-max_memory_size):]


        except Exception as e:
            print(f"更新记忆时出错: {e}")
            raise
