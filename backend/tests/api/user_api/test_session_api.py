"""
会话接口与对话接口的归属校验。只挂需要的路由，鉴权用 dependency_overrides 换成固定用户，
数据层换成临时 SQLite 上的 ChatMapper，不碰真实数据库、不调 LLM。
"""
import httpx
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.dao.chat_mapper as chat_mapper_module
from backend.api.dependencies import get_current_user
# 不能写成 import backend.api.user_api.agent_api as ...：backend/api/__init__.py 里有个
# 同名变量 user_api（APIRouter）把子包属性盖掉了，那种写法会沿着属性找、找到路由对象上去
from backend.api.user_api import agent_api
from backend.api.user_api.session_api import session_router
from backend.dao.chat_mapper import ChatMapper
from backend.model import Base
from backend.model.chat import ChatMessage, ChatSession  # noqa: F401  建表需要先导入模型
from backend.model.user import User

ALICE, BOB = User(id=1, username="alice"), User(id=2, username="bob")


@pytest_asyncio.fixture
async def mapper(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = ChatMapper(async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(chat_mapper_module, "_mapper", m)
    yield m
    await engine.dispose()


@pytest_asyncio.fixture
async def as_user(mapper):
    """返回一个工厂：as_user(ALICE) 得到以 Alice 身份发请求的客户端"""
    app = FastAPI()
    app.include_router(session_router)
    app.include_router(agent_api.agent_router)
    clients = []

    def make(user: User) -> httpx.AsyncClient:
        # 身份挂在共用的 app 上，后建的客户端会覆盖前一个的身份，所以一个测试只用一个身份
        async def current():
            return make.user
        make.user = user
        app.dependency_overrides[get_current_user] = current
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield make
    for c in clients:
        await c.aclose()


async def test_create_then_list(as_user):
    client = as_user(ALICE)
    created = (await client.post("/sessions")).json()
    assert created["title"] == "新对话"

    listed = (await client.get("/sessions")).json()
    assert [s["id"] for s in listed] == [created["id"]]


async def test_list_only_shows_own_sessions(as_user, mapper):
    await mapper.create_session(BOB.id)
    assert (await as_user(ALICE).get("/sessions")).json() == []


async def test_messages_of_own_session(as_user, mapper):
    s = await mapper.create_session(ALICE.id)
    await mapper.add_message(s["id"], "user", "问")
    await mapper.add_message(s["id"], "assistant", "答")

    body = (await as_user(ALICE).get(f"/sessions/{s['id']}/messages")).json()
    assert [(m["role"], m["content"]) for m in body] == [("user", "问"), ("assistant", "答")]


async def test_others_session_looks_like_missing(as_user, mapper):
    """别人的会话和不存在的会话返回完全一样的 404，没法据此枚举 id"""
    s = await mapper.create_session(BOB.id)
    client = as_user(ALICE)
    others = await client.get(f"/sessions/{s['id']}/messages")
    missing = await client.get("/sessions/999/messages")
    assert others.status_code == missing.status_code == 404
    assert others.json() == missing.json()


async def test_stream_rejects_others_session_before_writing(as_user, mapper, monkeypatch):
    """越权请求在开流之前就要被拦下，而且不能往别人的会话里写消息"""
    called = []
    monkeypatch.setattr(agent_api, "_stream_generator", lambda *a: called.append(a) or iter(()))
    s = await mapper.create_session(BOB.id)

    resp = await as_user(ALICE).post("/agent/analyse/stream", json={"session_id": s["id"], "text": "偷看"})
    assert resp.status_code == 404
    assert called == [], "被拒绝的请求不该启动 Agent"
    assert await mapper.list_messages(s["id"]) == []


async def test_stream_uses_token_identity_and_saves_question(as_user, mapper, monkeypatch):
    """请求体里塞别人的 user_id 不起作用：身份只认 token"""
    seen = []

    async def fake_stream(text, user_id, session_id):
        seen.append((text, user_id, session_id))
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_api, "_stream_generator", fake_stream)
    s = await mapper.create_session(ALICE.id)

    resp = await as_user(ALICE).post(
        "/agent/analyse/stream", json={"session_id": s["id"], "text": "出两道变式题", "user_id": BOB.id}
    )
    assert resp.status_code == 200
    assert seen == [("出两道变式题", ALICE.id, s["id"])]
    assert [(m["role"], m["content"]) for m in await mapper.list_messages(s["id"])] == [("user", "出两道变式题")]
