from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import get_short_term_memory
from backend.dao.user_profile_mapper import get_user_profile_mapper
from backend.schemas.request.ltm_request import LTMRequest


class UserProfileSaveInput(BaseModel):
    grade: Optional[str] = Field(default=None, description="用户年级，例如 '七年级'")
    subject: Optional[str] = Field(default=None, description="主修学科，例如 '数学'")
    weak_points: Optional[dict] = Field(default=None, description="薄弱知识点，JSON 对象")
    preferences: Optional[dict] = Field(default=None, description="长期偏好，JSON 对象")


class UserProfileSaveTool(BaseTool):
    name: str = "user_profile_save_tool"
    description: str = (
        "保存当前用户的长期画像：画像不存在则新建，已存在则按传入字段做局部更新。"
        "用户明确提到个人学情信息（年级/学科/薄弱知识点/长期偏好）时调用"
    )
    args_schema: Type[BaseModel] = UserProfileSaveInput

    async def _arun(
        self,
        user_id: Optional[int] = None,
        grade: Optional[str] = None,
        subject: Optional[str] = None,
        weak_points: Optional[dict] = None,
        preferences: Optional[dict] = None,
    ) -> str:
        if user_id is None:
            return "【用户画像】保存失败：缺少 user_id"
        try:
            mapper = await get_user_profile_mapper()
            stm = await get_short_term_memory()
            ltm = LongTermMemory(mapper, stm)

            request = LTMRequest(
                user_id=user_id,
                grade=grade,
                subject=subject,
                weak_points=weak_points,
                preferences=preferences,
            )
            await ltm.add_or_update(request)
            return f"【用户画像】已保存用户 {user_id} 的画像，请查询后确认"
        except Exception as e:
            return f"【用户画像】保存失败：{str(e)}"
