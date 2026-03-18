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
from datetime import datetime

from backend.agents.memory.short_term_memory import ShortTermMemory, get_short_term_memory
from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model.user_profile import UserProfile
from backend.schemas.DTO.LTMRequestDTO import LTMRequestDTO
from backend.schemas.DTO.user_profile_update_DTO import UserProfileUpdateDTO
from backend.dao.user_profile_mapper import get_user_profile_mapper
from fastapi import Depends

class LongTermMemory:
    def __init__(self,
                 user_profile_mapper:UserProfileMapper,
                 short_term_memory:ShortTermMemory):
        self.user_profile_mapper = user_profile_mapper
        self.short_term_memory = short_term_memory

    async def add_or_update(self,request:LTMRequestDTO) -> None:
        model = await self.user_profile_mapper.get_by_user_id(request.user_id)
        user_profile = UserProfileUpdateDTO.model_validate(model)
        if model is None:
            user_profile = UserProfile(
                user_id=request.user_id,
                grade=request.grade or "",
                subject=request.subject or "",
                preferences=request.preferences or "",
                weak_points=request.weak_points or [],
                create_time=datetime.now(),
                update_time=datetime.now()
            )
            await self.user_profile_mapper.create_memory(user_profile)
        else:
            user_profile.grade = request.grade or None
            user_profile.subject = request.subject or None
            user_profile.preferences = request.preferences or None
            user_profile.weak_points = request.weak_points or None
            user_profile.update_time = datetime.now()
            await self.user_profile_mapper.update_user_profile(user_profile)

    async def get_by_user_id(self,user_id:int) -> UserProfile | None:
        return await self.user_profile_mapper.get_by_user_id(user_id)

    async def delete(self,user_id:int) -> None:
        await self.user_profile_mapper.delete_memory(user_id)

    async def get_from_stm(self,user_id:int , session_id:str) -> list[str] | None:
        memories = await self.short_term_memory.get_latest_memories(user_id,session_id=session_id,limit=5)
        user_memory = list()
        for memory in memories:
            user_memory.append(memory["memory"]["user_memory"])
        return user_memory

async def get_long_term_memory(user_profile_mapper:UserProfileMapper = Depends(get_user_profile_mapper),
                               short_term_memory:ShortTermMemory = Depends(get_short_term_memory)) -> LongTermMemory:
        long_term_memory = LongTermMemory(user_profile_mapper,short_term_memory)
        return long_term_memory
