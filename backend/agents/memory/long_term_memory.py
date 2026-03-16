"""
这个程序用来写长期记忆，负责用户/业务的持久化，结构化数据存储
主要包含如下功能：
1. 长期记忆的增加和删除
2. 直接获取前n个长期记忆，或者通过元数据进行索引

选择用户画像来作为长期存储，并使用数据库进行存储，数据库的表结构如用户画像的Model文件所示：

主要技术分析：
1.用户画像该如何存储？需要考虑token成本和画像精准度
    考虑token成本：不能通过定时方法，让大模型分析用户现有的短期记忆并返回
    考虑画像精准度：就需要对几乎每一次对话都进行一个分析
    -> 综合考虑，可以选择在大模型回复的时候同时返回这个用户的画像，具体包括薄弱知识点和长期偏好
2. 但是在生题这个场景下貌似不太匹配，更加匹配的是短期记忆+RAG知识库
3. 如果确实要匹配的话，我可以进行如下设计
    用户画像，包括年级，长期偏好（经常问的知识点）
"""

from backend.agents.memory.short_term_memory import ShortTermMemory, get_short_term_memory
from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model.user_profile import UserProfile
from pydantic import BaseModel,Field
from fastapi import Depends
from backend.dao.user_profile_mapper import get_usr_profile_mapper


class LTMRequest(BaseModel):
    user_id: str = Field(...,description='用户唯一ID（业务主键）')
    grade: str = Field(...,description='年级')
    subject: str = Field(...,description='主修学科')
    preferences: str = Field(...,description='长期偏好（JSON）')


class LongTermMemory:
    def __init__(self,
                 user_profile_mapper:UserProfileMapper = Depends(get_usr_profile_mapper),
                 short_term_memory:ShortTermMemory = Depends(get_short_term_memory)):
        self.user_profile_mapper = user_profile_mapper
        self.short_term_memory = short_term_memory

    async def add_or_update(self,request:LTMRequest) -> None:
        user_profile = await self.user_profile_mapper.get_by_user_id(request.user_id)
        if user_profile is None:
            user_profile = UserProfile(
                user_id=request.user_id,
                grade=request.grade,
                subject=request.subject,
                preferences=request.preferences,
            )
            await self.user_profile_mapper.create_memory(user_profile)
        else:
            user_profile.grade = request.grade
            user_profile.subject = request.subject
            user_profile.preferences = request.preferences
            await self.user_profile_mapper.update_memory(user_profile)

    async def get_by_user_id(self,user_id:str) -> UserProfile | None:
        return await self.user_profile_mapper.get_by_user_id(user_id)

    async def delete(self,user_id:str) -> None:
        await self.user_profile_mapper.delete_memory(user_id)

    async def get_from_STM(self,user_id:str) -> list[str] | None:
        user_memory = await self.short_term_memory.get_latest_memories(limit=5)

