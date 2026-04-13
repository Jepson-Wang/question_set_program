from typing import Type, Any

from backend.agents.agent.common_agent import async_common_tool, common_tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

class CommonInput(BaseModel):
    query : str = Field(description="用户的查询内容")

class CommonSKill(BaseTool):
    name :str = "common_skill"
    description : str = "通用技能，处理基本的用户请求和信息查询，包括解题步骤、思路、方法、答案等"
    args_schema : Type[BaseModel] = CommonInput

    def _run(self, query: str) -> str:
        result = common_tool(query)
        return result

    async def _arun(self, query: str) -> str:
        """执行通用技能"""
        # 使用 common_agent 处理查询
        result = await async_common_tool(query)
        return result