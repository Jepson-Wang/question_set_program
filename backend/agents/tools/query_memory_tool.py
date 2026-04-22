from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.agents.memory.short_term_memory import get_short_term_memory


class QueryMemoryInput(BaseModel):
    limit: int = Field(default=5, description="返回的记忆条数")


class QueryMemoryTool(BaseTool):
    name: str = "query_memory_tool"
    description: str = "查询当前用户的历史对话记忆，用于了解用户过去的问题、偏好或上下文"
    args_schema: Type[BaseModel] = QueryMemoryInput

    async def _arun(self, user_id: Optional[int] = None, session_id: Optional[int] = None, limit: int = 5) -> str:
        """异步执行记忆查询"""
        try:
            memory = await get_short_term_memory()
            memories = await memory.get_latest_memories(user_id, session_id, limit)

            if not memories:
                return "【记忆查询】未找到相关记忆"

            lines = []
            for i, mem in enumerate(memories, 1):
                user_content = mem.get('user', '')
                model_content = mem.get('model', '')
                timestamp = mem.get('timestamp', '')
                lines.append(f"{i}. 时间: {timestamp}\n用户: {user_content}\n模型: {model_content}")

            return "【记忆查询】找到以下记忆：\n" + "\n".join(lines)
        except Exception as e:
            return f"【记忆查询】查询失败：{str(e)}"
