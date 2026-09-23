"""
记忆表的数据访问测试。跑在临时的 SQLite 文件库上：DAO 只依赖会话工厂，
换个数据库照样能测，不需要本机装 MySQL，也不会碰到生产库。
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.dao.memory_mapper import MemoryMapper
from backend.model import Base
from backend.model.memory import MemoryDigest, MemoryRecord  # noqa: F401  建表需要先导入模型

USER, SESSION = 7, 7


@pytest_asyncio.fixture
async def mapper(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield MemoryMapper(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def _add(mapper, n: int, session_id: int = SESSION) -> None:
    for i in range(n):
        await mapper.add_record(USER, session_id, f"问题{i}", f"回答{i}", f"fp{session_id}-{i}")


async def test_records_come_back_oldest_first(mapper):
    """摘要要按时序读，所以取出来必须是从旧到新"""
    await _add(mapper, 3)
    assert [r["user_text"] for r in await mapper.list_records(USER, SESSION, limit=10)] == ["问题0", "问题1", "问题2"]


async def test_limit_keeps_the_newest(mapper):
    """超过上限时保留最近的几条，而不是最早的几条"""
    await _add(mapper, 5)
    assert [r["user_text"] for r in await mapper.list_records(USER, SESSION, limit=2)] == ["问题3", "问题4"]


async def test_duplicate_fingerprint_is_ignored(mapper):
    """归档是「至少一次」，重试会把同一条再写一遍；靠唯一键挡住，不能出现两条"""
    assert await mapper.add_record(USER, SESSION, "问题", "回答", "same-fp") is True
    assert await mapper.add_record(USER, SESSION, "问题", "回答", "same-fp") is False
    assert len(await mapper.list_records(USER, SESSION, limit=10)) == 1


async def test_sessions_are_isolated(mapper):
    await _add(mapper, 2, session_id=1)
    await _add(mapper, 3, session_id=2)
    assert len(await mapper.list_records(USER, 1, limit=10)) == 2
    assert len(await mapper.list_records(USER, 2, limit=10)) == 3


async def test_count_since_tracks_uncovered_records(mapper):
    await _add(mapper, 3)
    pending, latest = await mapper.count_since(USER, SESSION, after_id=0)
    assert (pending, latest) == (3, 3)
    pending, latest = await mapper.count_since(USER, SESSION, after_id=2)
    assert (pending, latest) == (1, 3)


async def test_count_since_on_empty_session(mapper):
    assert await mapper.count_since(USER, 999, after_id=0) == (0, 0)


async def test_digest_is_created_then_updated(mapper):
    assert await mapper.get_digest(USER, SESSION) is None

    await mapper.save_digest(USER, SESSION, "第一版摘要", covered_until_id=3)
    first = await mapper.get_digest(USER, SESSION)
    assert (first.summary, first.covered_until_id) == ("第一版摘要", 3)

    await mapper.save_digest(USER, SESSION, "第二版摘要", covered_until_id=8)
    second = await mapper.get_digest(USER, SESSION)
    assert (second.summary, second.covered_until_id) == ("第二版摘要", 8)
    assert len(await mapper.list_records(USER, SESSION, limit=10)) == 0, "保存摘要不应影响原文"
