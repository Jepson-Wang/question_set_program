from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from backend.model import get_db
from backend.model.user_profile import UserProfile
from typing import Optional, Dict, Any, List
from fastapi import Depends


class UserProfileMapper:
    def __init__(self, db_session: AsyncSession):
        self.session = db_session

    async def create_memory(self, user_profile : UserProfile):
        """创建用户画像记录"""
        user_profile = UserProfile(
            user_id= user_profile.user_id,
            grade= user_profile.grade,
            subject= user_profile.subject,
            weak_points= user_profile.weak_points,
            preferences= user_profile.preferences
        )
        self.session.add(user_profile)
        await self.session.commit()
        await self.session.refresh(user_profile)
        return user_profile

    async def get_by_user_id(self, user_id: str) -> Optional[UserProfile]:
        """根据用户ID获取用户画像"""
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: int) -> Optional[UserProfile]:
        """根据ID获取用户画像"""
        stmt = select(UserProfile).where(UserProfile.id == profile_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_memory(self, user_id: str, **kwargs) -> Optional[UserProfile]:
        """更新用户画像记录"""
        user_profile = await self.get_by_user_id(user_id)
        if user_profile:
            for key, value in kwargs.items():
                if hasattr(user_profile, key):
                    setattr(user_profile, key, value)
            await self.session.commit()
            await self.session.refresh(user_profile)
        return user_profile

    async def delete_memory(self, user_id: str) -> bool:
        """删除用户画像记录"""
        user_profile = await self.get_by_user_id(user_id)
        if user_profile:
            await self.session.delete(user_profile)
            await self.session.commit()
            return True
        return False

    async def get_all(self, offset: int = 0, limit: int = 100) -> List[UserProfile]:
        """获取所有用户画像（支持分页）"""
        stmt = select(UserProfile).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
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