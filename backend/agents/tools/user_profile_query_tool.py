from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import get_short_term_memory
from backend.dao.user_profile_mapper import get_user_profile_mapper
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


class UserProfileQueryInput(BaseModel):
    pass


class UserProfileQueryTool(BaseTool):
    name: str = "user_profile_query_tool"
    description: str = (
        "查询当前用户的长期画像（年级、学科、薄弱知识点、长期偏好），用于个性化回答"
        "添加或更新用户画像前必须调用"
    )
    args_schema: Type[BaseModel] = UserProfileQueryInput

    async def _arun(self, user_id: Optional[int] = None) -> str:
        logger.info("查询用户画像，user_id: %s", user_id)
        if user_id is None:
            return "【用户画像】查询失败：缺少 user_id"
        try:
            mapper = await get_user_profile_mapper()
            stm = await get_short_term_memory()
            ltm = LongTermMemory(mapper, stm)

            profile = await ltm.get_by_user_id(user_id)
            if profile is None:
                return f"【用户画像】用户 {user_id} 尚未创建画像"

            data = profile.model_dump()
            logger.info("用户画像查询成功")
            return (
                f"【用户画像】用户 {user_id} 的画像：\n"
                f"年级：{data.get('grade', '未知')}\n"
                f"学科：{data.get('subject', '未知')}\n"
                f"薄弱知识点：{data.get('weak_points', {})}\n"
                f"长期偏好：{data.get('preferences', {})}"
            )
        except Exception as e:
            return f"【用户画像】查询失败：{str(e)}"
