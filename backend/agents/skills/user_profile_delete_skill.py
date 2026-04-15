import asyncio
from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import get_short_term_memory
from backend.dao.user_profile_mapper import get_user_profile_mapper


class UserProfileDeleteInput(BaseModel):
    pass


class UserProfileDeleteSkill(BaseTool):
    name: str = "user_profile_delete_skill"
    description: str = "删除当前用户的长期画像。仅在用户明确要求清空个人资料时调用"
    args_schema: Type[BaseModel] = UserProfileDeleteInput

    def _run(self, user_id: Optional[int] = None) -> str:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._arun(user_id=user_id))
        except RuntimeError:
            return "【用户画像】删除失败：无法在当前事件循环中执行"

    async def _arun(self, user_id: Optional[int] = None) -> str:
        if user_id is None:
            return "【用户画像】删除失败：缺少 user_id"
        try:
            mapper = await get_user_profile_mapper()
            stm = await get_short_term_memory()
            ltm = LongTermMemory(mapper, stm)

            ok = await ltm.delete(user_id)
            if ok:
                return f"【用户画像】已删除用户 {user_id} 的画像"
            return f"【用户画像】用户 {user_id} 无画像可删"
        except Exception as e:
            return f"【用户画像】删除失败：{str(e)}"
