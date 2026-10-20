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

from fastapi import Depends

from backend.agents.memory.short_term_memory import ShortTermMemory, get_short_term_memory
from backend.dao.exceptions import ProfileAlreadyExistsError
from backend.dao.user_profile_mapper import UserProfileMapper, get_user_profile_mapper
from backend.model.user_profile import UserProfile
from backend.schemas.request.ltm_request import LTMRequest
from backend.schemas.request.user_profile_update_request import UserProfileUpdateRequest
from backend.schemas.response.user_profile_response import UserProfileResponse


class LongTermMemory:
    def __init__(self,
                 user_profile_mapper: UserProfileMapper,
                 short_term_memory: ShortTermMemory):
        self.user_profile_mapper = user_profile_mapper
        self.short_term_memory = short_term_memory

    async def add_or_update(self, request: LTMRequest) -> None:
        """
        若用户画像不存在则创建，存在则部分更新（只覆盖 request 中非 None 的字段）。

        失败只有一种信号：抛异常。正常返回就代表已经落库，调用方据此清理候选池，
        所以这里不能有「没写进去却正常返回」的路径。
        """
        # 预查询是快速路径，不负责正确性：绝大多数调用走更新，总是先 INSERT 的话
        # 每次都白失败一次，还会白白消耗自增 id。并发首次建档的窗口由下面的
        # ProfileAlreadyExistsError 兜住，判定谁先建档的是 user_id 的唯一约束
        existing = await self.user_profile_mapper.get_by_user_id(request.user_id)

        if existing is None:
            # 新建：weak_points/preferences 是 NOT NULL 的 JSON 字段，用空 dict 兜底
            new_profile = UserProfile(
                user_id=request.user_id,
                grade=request.grade or "",
                subject=request.subject or "数学",
                preferences=request.preferences or {},
                weak_points=request.weak_points or {},
                notes=request.notes or [],
                create_time=datetime.now(),
                update_time=datetime.now(),
            )
            try:
                await self.user_profile_mapper.create_memory(new_profile)
                return
            except ProfileAlreadyExistsError:
                # 预查询之后、INSERT 之前别人抢先建了档：不算失败，往下走更新，
                # 把这次的内容合并进去
                pass

        # 部分更新：只传入非 None 字段，mapper 内部通过 exclude_none 过滤
        update_dto = UserProfileUpdateRequest(
            user_id=request.user_id,
            grade=request.grade,
            subject=request.subject,
            preferences=request.preferences,
            weak_points=request.weak_points,
            notes=request.notes,
            update_time=datetime.now(),
        )
        # 画像在预查询之后被删掉时抛 ProfileNotFoundError，不在这里接：
        # 让它冒到工具层判失败，候选得以保留
        await self.user_profile_mapper.update_user_profile(update_dto)

    async def get_by_user_id(self, user_id: int) -> UserProfileResponse | None:
        return await self.user_profile_mapper.get_by_user_id(user_id)

    async def delete(self, user_id: int) -> bool:
        return await self.user_profile_mapper.delete_memory(user_id)

    async def get_from_stm(self, user_id: int, session_id: int) -> list[str]:
        """从短期记忆中提取用户发言列表，供画像生成使用。"""
        memories = await self.short_term_memory.get_latest_memories(
            user_id, session_id=session_id, limit=5
        )
        return [memory["memory"]["user_memory"] for memory in memories]


async def get_long_term_memory(
    user_profile_mapper: UserProfileMapper = Depends(get_user_profile_mapper),
    short_term_memory: ShortTermMemory = Depends(get_short_term_memory),
) -> LongTermMemory:
    return LongTermMemory(user_profile_mapper, short_term_memory)
