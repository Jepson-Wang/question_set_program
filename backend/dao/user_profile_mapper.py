from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.dao.exceptions import ProfileAlreadyExistsError, ProfileNotFoundError
from backend.model import AsyncSessionLocal  # 导入会话工厂
from backend.model.user_profile import UserProfile
from typing import Optional

from backend.schemas.request.user_profile_update_request import UserProfileUpdateRequest
from backend.schemas.response.user_profile_response import UserProfileResponse

# 这两个字段需要合并而不是替换
_MERGE_FIELDS = ("weak_points", "preferences")

# notes是自由文本数组， 语义是追加而不是合并
_APPEND_FIELDS = ("notes",)

NOTES_MAX_SIZE = 50

def merge_json_fields(old: dict | None,new: dict | None) -> dict:
    """服务于 weak_points 和 preferences：新键加入，同名键以新值覆盖，没提到的老键保留"""
    merged = dict(old or {})
    merged.update(new or {})
    return merged

def append_notes(old: list | None, new: list | None, max_size: int = NOTES_MAX_SIZE) -> list:
    """服务于notes, 用于加入，更新和删除多余元素"""
    result = list(old or [])
    for item in new or []:
        if item not in result:
            result.append(item)
    if len(result) > max_size:
        result = result[-max_size:]
    return result

_MYSQL_DUP_ENTRY = 1062


def _is_duplicate_key(e: IntegrityError) -> bool:
    """
    IntegrityError 还包括 NOT NULL（1048）、外键（1452）等，只有唯一键冲突才意味着
    「画像已存在」。这张表除了自增主键只有 user_id 一个唯一约束，认出唯一键冲突就够了。
    生产是 MySQL，测试跑在 SQLite 上，两边的报错形态不同，都要认。
    """
    args = getattr(e.orig, "args", ())
    if args and args[0] == _MYSQL_DUP_ENTRY:
        return True
    return "UNIQUE constraint failed" in str(e.orig)


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
                    preferences=user_profile.preferences,
                    notes=user_profile.notes or [],
                )
                session.add(new_profile)
                await session.commit()
                await session.refresh(new_profile)
                return new_profile
            # IntegrityError 是 SQLAlchemyError 的子类，必须写在前面，否则永远匹配不到
            except IntegrityError as e:
                await session.rollback()
                if _is_duplicate_key(e):
                    raise ProfileAlreadyExistsError(user_profile.user_id) from e
                raise
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_user_id(self, user_id: int) -> Optional[UserProfileResponse]:
        """根据用户ID获取画像"""
        async with self.session_factory() as session:
            stmt = select(UserProfile).where(UserProfile.user_id == user_id)
            result = await session.execute(stmt)
            user_profile = result.scalar_one_or_none()
            if not user_profile:
                return None
            return UserProfileResponse.model_validate(user_profile)

    async def update_user_profile(self, profile_dto: UserProfileUpdateRequest) -> UserProfileResponse:
        """
        更新用户画像。画像不存在时抛 ProfileNotFoundError 而不是返回 None：
        调用方只要漏查一次返回值，「没写进去」就会被当成「写成功了」，
        而候选池会据此删掉候选，这条偏好就丢了。
        """
        # 非法 id 是调用方的 bug，和「画像不存在」不是一回事，不混成同一个异常
        if profile_dto.user_id < 0:
            raise ValueError(f"非法的 user_id：{profile_dto.user_id}")

        async with self.session_factory() as session:
            try:
                stmt = select(UserProfile).where(UserProfile.user_id == profile_dto.user_id)
                result = await session.execute(stmt)
                user_profile = result.scalar_one_or_none()

                if user_profile is None:
                    raise ProfileNotFoundError(profile_dto.user_id)

                dto_data = profile_dto.model_dump(exclude_none=True)
                for key, value in dto_data.items():
                    if not hasattr(user_profile, key):
                        continue
                    if key in _MERGE_FIELDS:
                        setattr(user_profile, key, merge_json_fields(
                            getattr(user_profile, key), value
                        ))
                    elif key in _APPEND_FIELDS:
                        setattr(user_profile, key, append_notes(
                            getattr(user_profile, key), value
                        ))
                    else:
                        setattr(user_profile, key, value)

                await session.commit()
                await session.refresh(user_profile)
                return UserProfileResponse.model_validate(user_profile)
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def delete_memory(self, user_id: int) -> bool:
        """
        删除用户画像。必须在同一个 session 内查询 ORM 对象再删除，
        不能用 get_by_user_id 的返回值（那是 DTO，不是 ORM 实体）。
        """
        async with self.session_factory() as session:
            try:
                stmt = select(UserProfile).where(UserProfile.user_id == user_id)
                result = await session.execute(stmt)
                user_profile = result.scalar_one_or_none()
                if user_profile is None:
                    return False
                await session.delete(user_profile)
                await session.commit()
                return True
            except SQLAlchemyError:
                await session.rollback()
                raise


    # 其他方法（delete_memory/get_all/update_weak_points 等）按相同逻辑改造：
    # 1. async with self.session_factory() as session:
    # 2. 所有操作使用 session 变量，而非 self.session_factory
    # 3. 添加 try/except/rollback 保证事务安全


# 依赖函数改为注入工厂，而非实例
async def get_user_profile_mapper() -> UserProfileMapper:
    # 直接传入全局工厂，无需 Depends(get_db)
    return UserProfileMapper(AsyncSessionLocal)
