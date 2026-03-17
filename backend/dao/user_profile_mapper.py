from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from backend.model import get_db
from backend.model.user_profile import UserProfile
from typing import Optional, Dict, Any, List
from fastapi import Depends

from backend.schemas.DTO.user_profile_update_DTO import UserProfileUpdateDTO
from backend.schemas.VO.user_profile_VO import UserProfileVO


class UserProfileMapper:
    def __init__(self, db_session: AsyncSession):
        self.session = db_session

    async def create_memory(self, user_profile : UserProfile):
        """创建用户画像记录"""
        async with self.session as session:
            user_profile = UserProfile(
                user_id=user_profile.user_id,
                grade=user_profile.grade,
                subject=user_profile.subject,
                weak_points=user_profile.weak_points,
                preferences=user_profile.preferences
            )
            session.add(user_profile)
            await self.session.commit()
            await self.session.refresh(user_profile)
            return user_profile

    async def get_by_user_id(self, user_id: str) -> Optional[UserProfileVO]:
        """根据用户ID获取用户画像"""
        async with self.session as session:
            stmt = select(UserProfile).where(UserProfile.user_id == user_id)
            user_profile = await session.execute(stmt)
            ans = UserProfileVO.model_validate(user_profile)
            return ans

    async def get_by_id(self, profile_id: int) -> Optional[UserProfileVO]:
        """根据ID获取用户画像"""
        async with self.session as session:
            stmt = select(UserProfile).where(UserProfile.id == profile_id)
            user_profile = await session.execute(stmt)
            ans = UserProfileVO.model_validate(user_profile)
            return ans

    async def update_memory(self, user_profile : UserProfileUpdateDTO) -> Optional[UserProfileUpdateDTO]:
        """更新用户画像记录"""
        async with self.session as session:
            user_profile = await self.get_by_user_id(user_profile.user_id)
            if user_profile:
                for key, value in user_profile.model_dump().items():
                    if hasattr(user_profile, key) and value is not None:
                        setattr(user_profile, key, value)
            await session.commit()
            await session.refresh(user_profile)
        return user_profile

    async def delete_memory(self, user_id: str) -> bool:
        """删除用户画像记录"""
        async with self.session as session:
            user_profile = await self.get_by_user_id(user_id)
            if user_profile:
                await session.delete(user_profile)
                await session.commit()
                return True
        return False

    async def get_all(self, offset: int = 0, limit: int = 100) -> List[UserProfile]:
        """获取所有用户画像（支持分页）"""
        async with self.session as session:
            stmt = select(UserProfile).offset(offset).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_weak_points(self, user_id: str, weak_points: Dict[str, Any]) -> Optional[UserProfile]:
        """更新薄弱知识点"""
        return await self.update_memory(user_id, weak_points=weak_points)

    async def update_preferences(self, user_id: str, preferences: Dict[str, Any]) -> Optional[UserProfile]:
        """更新用户偏好"""
        return await self.update_memory(user_id, preferences=preferences)

    async def update_grade(self, user_id: str, grade: str) -> Optional[UserProfile]:
        """更新年级"""
        return await self.update_memory(user_id, grade=grade)

    async def update_subject(self, user_id: str, subject: str) -> Optional[UserProfile]:
        """更新学科"""
        return await self.update_memory(user_id, subject=subject)

async def get_usr_profile_mapper(db : AsyncSession = Depends(get_db)) -> UserProfileMapper:
    return UserProfileMapper(db)