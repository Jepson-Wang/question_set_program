"""
会话与消息表的数据访问测试，和 test_memory_mapper 一样跑在临时 SQLite 库上。
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.dao.chat_mapper import DEFAULT_TITLE, ChatMapper
from backend.model import Base
from backend.model.chat import ChatMessage, ChatSession  # noqa: F401  建表需要先导入模型

ALICE, BOB = 1, 2


@pytest_asyncio.fixture
async def mapper(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield ChatMapper(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_new_session_has_default_title(mapper):
    s = await mapper.create_session(ALICE)
    assert s["title"] == DEFAULT_TITLE
    assert s["created_at"] == s["updated_at"]


async def test_latest_active_session_comes_first(mapper):
    """侧边栏按最后活跃排序：给旧会话发一条消息，它要跳到最前面"""
    old = await mapper.create_session(ALICE)
    new = await mapper.create_session(ALICE)
    assert [s["id"] for s in await mapper.list_sessions(ALICE, limit=10)] == [new["id"], old["id"]]

    await mapper.add_message(old["id"], "user", "再问一个")
    assert [s["id"] for s in await mapper.list_sessions(ALICE, limit=10)] == [old["id"], new["id"]]


async def test_list_respects_limit(mapper):
    for _ in range(3):
        await mapper.create_session(ALICE)
    assert len(await mapper.list_sessions(ALICE, limit=2)) == 2


async def test_users_cannot_see_each_others_sessions(mapper):
    mine = await mapper.create_session(ALICE)
    await mapper.create_session(BOB)

    assert [s["id"] for s in await mapper.list_sessions(ALICE, limit=10)] == [mine["id"]]
    assert await mapper.get_session(ALICE, mine["id"]) is not None
    assert await mapper.get_session(BOB, mine["id"]) is None, "别人的会话要和不存在一样处理"


async def test_get_missing_session(mapper):
    assert await mapper.get_session(ALICE, 999) is None


async def test_messages_come_back_oldest_first(mapper):
    s = await mapper.create_session(ALICE)
    await mapper.add_message(s["id"], "user", "问")
    await mapper.add_message(s["id"], "assistant", "答")
    msgs = await mapper.list_messages(s["id"])
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "问"), ("assistant", "答")]


async def test_messages_are_isolated_by_session(mapper):
    a = await mapper.create_session(ALICE)
    b = await mapper.create_session(ALICE)
    await mapper.add_message(a["id"], "user", "A 的问题")
    assert await mapper.list_messages(b["id"]) == []


async def test_first_question_becomes_title(mapper):
    s = await mapper.create_session(ALICE)
    await mapper.add_message(s["id"], "user", "  帮我出三道\n一元二次方程的变式题  ")
    await mapper.add_message(s["id"], "user", "第二个问题不该改标题")
    assert (await mapper.get_session(ALICE, s["id"]))["title"] == "帮我出三道 一元二次方程的变式题"


async def test_title_is_truncated(mapper):
    s = await mapper.create_session(ALICE)
    await mapper.add_message(s["id"], "user", "题" * 100)
    assert (await mapper.get_session(ALICE, s["id"]))["title"] == "题" * 30


async def test_assistant_message_does_not_set_title(mapper):
    s = await mapper.create_session(ALICE)
    await mapper.add_message(s["id"], "assistant", "你好，有什么可以帮你？")
    assert (await mapper.get_session(ALICE, s["id"]))["title"] == DEFAULT_TITLE


async def test_blank_question_keeps_default_title(mapper):
    s = await mapper.create_session(ALICE)
    await mapper.add_message(s["id"], "user", "   \n ")
    assert (await mapper.get_session(ALICE, s["id"]))["title"] == DEFAULT_TITLE
