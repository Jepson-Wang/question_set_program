from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # 补充导入
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError

from backend.model import AsyncSessionLocal  # 导入会话工厂
from backend.model.user_profile import UserProfile
from typing import Optional, Dict, Any, List

from backend.schemas.DTO.user_profile_update_DTO import UserProfileUpdateDTO
from backend.schemas.VO.user_profile_VO import UserProfileVO
from fastapi import Depends

# 定义 AsyncSessionLocal 类型（方便类型提示）
AsyncSessionLocal = async_sessionmaker[AsyncSession]


class UserProfileMapper:
    def __init__(self, session_factory: AsyncSessionLocal):
        self.session_factory = session_factory  # 接收工厂，而非实例

    async def create_memory(self, user_profile: UserProfile) -> UserProfile:
        """创建用户画像（正确使用工厂）"""
        # ✅ 工厂调用 () 生成新会话，async with 管理生命周期
        async with self.session_factory() as session:
            try:
                new_profile = UserProfile(
                    user_id=user_profile.user_id,
                    grade=user_profile.grade,
                    subject=user_profile.subject,
                    weak_points=user_profile.weak_points,
                    preferences=user_profile.preferences
                )
                session.add(new_profile)
                await session.commit()
                await session.refresh(new_profile)
                return new_profile
            except SQLAlchemyError as e:
                await session.rollback()
                raise e

    async def get_by_user_id(self, user_id: int) -> Optional[UserProfileVO]:
        """根据用户ID获取画像"""
        async with self.session_factory() as session:
            stmt = select(UserProfile).where(UserProfile.user_id == user_id)
            result = await session.execute(stmt)
            user_profile = result.scalar_one_or_none()
            if not user_profile:
                return None
            return UserProfileVO.model_validate(user_profile)

    async def update_user_profile(self, profile_dto: UserProfileUpdateDTO) -> Optional[UserProfileVO]:
        """更新用户画像"""
        async with self.session_factory() as session:
            try:
                stmt = select(UserProfile).where(UserProfile.user_id == profile_dto.user_id)
                result = await session.execute(stmt)
                user_profile = result.scalar_one_or_none()

                if not user_profile or profile_dto.user_id<0:
                    return None

                dto_data = profile_dto.model_dump(exclude_none=True)
                for key, value in dto_data.items():
                    if hasattr(user_profile, key):
                        setattr(user_profile, key, value)

                await session.commit()
                await session.refresh(user_profile)
                return UserProfileVO.model_validate(user_profile)
            except SQLAlchemyError as e:
                await session.rollback()
                raise e

    async def delete_memory(self , user_id: int) -> bool:
        """
        删除用户画像
        :param user_id:
        :return:
        """
        async with self.session_factory as session:
            try:
                user_profile = await self.get_by_user_id(user_id)
                if user_profile:
                    await session.delete(user_profile)
                    await session.commit()
                    return True
                return False
            except SQLAlchemyError as e:
                await session.rollback()
                raise e


    # 其他方法（delete_memory/get_all/update_weak_points 等）按相同逻辑改造：
    # 1. async with self.session_factory() as session:
    # 2. 所有操作使用 session 变量，而非 self.session_factory
    # 3. 添加 try/except/rollback 保证事务安全


# 依赖函数改为注入工厂，而非实例
async def get_user_profile_mapper() -> UserProfileMapper:
    # 直接传入全局工厂，无需 Depends(get_db)
    return UserProfileMapper(AsyncSessionLocal)