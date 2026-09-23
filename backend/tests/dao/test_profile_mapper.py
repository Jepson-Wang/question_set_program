"""
画像写入策略的落库测试，跑在临时 SQLite 上。

为什么光有 test_profile_merge.py 不够：那些测试只调用 merge_json_fields /
append_notes 这两个纯函数。函数写对了，但 mapper 里按字段名分派时把列名写成
了单数（weak_point / preference），一个都命中不了，于是全部走 else 分支整体
覆盖——Task 8 要解决的问题原封不动，而那 8 条测试全绿。

所以这里测的是「分派有没有接上」，不是「合并算得对不对」。
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model import Base
from backend.model.user_profile import UserProfile  # noqa: F401  建表需要先导入模型
from backend.schemas.request.user_profile_update_request import UserProfileUpdateRequest

USER = 7


@pytest_asyncio.fixture
async def mapper(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield UserProfileMapper(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def _seed(mapper, **kwargs) -> None:
    await mapper.create_memory(UserProfile(
        user_id=USER,
        grade=kwargs.get("grade", "七年级"),
        subject=kwargs.get("subject", "数学"),
        weak_points=kwargs.get("weak_points", {}),
        preferences=kwargs.get("preferences", {}),
        notes=kwargs.get("notes", []),
    ))


async def test_new_weak_point_does_not_drop_existing(mapper):
    """核心保护：写入一个新知识点，已有的必须还在"""
    await _seed(mapper, weak_points={"函数": "薄弱", "几何": "薄弱"})
    got = await mapper.update_user_profile(
        UserProfileUpdateRequest(user_id=USER, weak_points={"方程": "薄弱"})
    )
    assert got.weak_points == {"函数": "薄弱", "几何": "薄弱", "方程": "薄弱"}


async def test_new_preference_does_not_drop_existing(mapper):
    await _seed(mapper, preferences={"学习方式": "视频"})
    got = await mapper.update_user_profile(
        UserProfileUpdateRequest(user_id=USER, preferences={"做题时段": "晚上"})
    )
    assert got.preferences == {"学习方式": "视频", "做题时段": "晚上"}


async def test_notes_are_appended(mapper):
    await _seed(mapper, notes=["做题时喜欢先看思路"])
    got = await mapper.update_user_profile(
        UserProfileUpdateRequest(user_id=USER, notes=["晚上做题效率更高"])
    )
    assert got.notes == ["做题时喜欢先看思路", "晚上做题效率更高"]


async def test_plain_fields_are_still_replaced(mapper):
    """不在合并/追加名单里的字段维持覆盖语义，别把分派写成「什么都合并」"""
    await _seed(mapper, grade="七年级")
    got = await mapper.update_user_profile(
        UserProfileUpdateRequest(user_id=USER, grade="八年级")
    )
    assert got.grade == "八年级"


async def test_create_persists_notes(mapper):
    """新建画像这条路径也要带上 notes，只改更新路径的话这里会静默丢掉"""
    await _seed(mapper, notes=["第一条观察"])
    got = await mapper.get_by_user_id(USER)
    assert got.notes == ["第一条观察"]


async def test_update_on_missing_profile_returns_none(mapper):
    assert await mapper.update_user_profile(
        UserProfileUpdateRequest(user_id=999, grade="八年级")
    ) is None
