"""
add_or_update 的两条竞态路径，跑在临时 SQLite 上。

真实的竞态窗口在「预查询」和「INSERT / UPDATE」之间，靠并发去撞不稳定。
这里直接让预查询返回一个过时的结果，把窗口固定下来，结果是确定的。
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.dao.exceptions import ProfileNotFoundError
from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model import Base
from backend.model.user_profile import UserProfile
from backend.schemas.request.ltm_request import LTMRequest

USER = 7


@pytest_asyncio.fixture
async def mapper(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield UserProfileMapper(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_concurrent_first_create_merges_instead_of_failing(mapper, monkeypatch):
    """
    预查询说「不存在」，INSERT 时别人已经建好了档：应该合并进去，而不是报「保存失败」。
    改之前这里会抛 IntegrityError。
    """
    await mapper.create_memory(UserProfile(
        user_id=USER, grade="七年级", subject="数学",
        weak_points={}, preferences={"题目风格": "简单"}, notes=[],
    ))

    async def stale_lookup(user_id):
        return None
    monkeypatch.setattr(mapper, "get_by_user_id", stale_lookup)

    ltm = LongTermMemory(mapper, short_term_memory=None)
    await ltm.add_or_update(LTMRequest(user_id=USER, preferences={"讲解详细度": "详细"}))

    monkeypatch.undo()
    got = await mapper.get_by_user_id(USER)
    assert got.preferences == {"题目风格": "简单", "讲解详细度": "详细"}


async def test_profile_deleted_after_lookup_is_not_silent(mapper, monkeypatch):
    """
    预查询说「存在」，UPDATE 时画像已被删掉：必须抛出来。
    以前这里静默返回，调用方会以为落库成功，进而删掉候选，偏好就丢了。
    """
    async def stale_lookup(user_id):
        return object()  # 只要不是 None，add_or_update 就会走更新分支
    monkeypatch.setattr(mapper, "get_by_user_id", stale_lookup)

    ltm = LongTermMemory(mapper, short_term_memory=None)
    with pytest.raises(ProfileNotFoundError):
        await ltm.add_or_update(LTMRequest(user_id=USER, preferences={"讲解详细度": "详细"}))
