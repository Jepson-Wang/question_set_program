"""
并发回归测试：同一会话的请求必须排队跑。

背景：不加锁时，两个并发请求会同时跑，落库顺序变成「完成顺序」而不是「提问顺序」，
user1 的提问会配上 user2 的回答；两次请求各自读到的上下文快照也会互相踩。

底座沿用 test_session_api.py：临时 SQLite + ChatMapper，鉴权用 dependency_overrides
换成固定用户，记忆和 Agent 换成假的，不碰真实数据库、不调 LLM。
"""
import asyncio
import json
import re
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.dao.chat_mapper as chat_mapper_module
from backend.api.dependencies import get_current_user
from backend.api.user_api import agent_api
from backend.api.user_api.session_api import session_router
from backend.core.session_lock import SessionLockPool
from backend.dao.chat_mapper import ChatMapper
from backend.model import Base
from backend.model.chat import ChatMessage, ChatSession  # noqa: F401  建表需要先导入模型
from backend.model.user import User

ALICE = User(id=1, username="alice")


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
async def client(mapper, monkeypatch):
    """以 Alice 身份发请求的客户端：记忆换成假的，锁池每个测试一份新的"""

    async def fake_get_memory(user_id, session_id):
        return {"short_memory": [], "long_memory": None, "session_digest": ""}

    async def fake_add_memory(user_id, session_id, memory):
        return None

    monkeypatch.setattr(
        agent_api,
        "memory_manager",
        SimpleNamespace(
            get_memory_for_planner=fake_get_memory,
            add_memory=fake_add_memory,
        ),
    )
    # 每个测试一个全新的锁池：上一个测试的残留不该影响下一个。
    # 池里的 asyncio.Lock 绑定了事件循环，跨测试复用还会直接报错。
    monkeypatch.setattr(agent_api, "session_locks", SessionLockPool())

    app = FastAPI()
    app.include_router(session_router)
    app.include_router(agent_api.agent_router)

    async def current():
        return ALICE

    app.dependency_overrides[get_current_user] = current

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_same_session_requests_are_serialized(client, mapper, monkeypatch):
    """
    同一会话的两个并发请求必须互斥，落库顺序 = 提问顺序。

    不加锁时两个请求会同时跑，落库顺序变成「完成顺序」：
    user1、user2、assistant1、assistant2——问答配对直接串味。
    """
    events = []
    entered = asyncio.Event()
    release = asyncio.Event()

    class _FakeApp:
        async def ainvoke(self, state):
            events.append(("enter", state["user_input"]))
            entered.set()
            await release.wait()
            events.append(("exit", state["user_input"]))
            return {"final_result": f"答:{state['user_input']}"}

    monkeypatch.setattr(agent_api, "get_app", lambda: _FakeApp())

    sid = (await mapper.create_session(ALICE.id))["id"]

    first = asyncio.create_task(
        client.post("/agent/analyse", json={"session_id": sid, "text": "问一"})
    )
    # 等第一个请求真的拿到锁跑起来了，再发第二个，顺序才是确定的
    await asyncio.wait_for(entered.wait(), timeout=2)

    second = asyncio.create_task(
        client.post("/agent/analyse", json={"session_id": sid, "text": "问二"})
    )
    await asyncio.sleep(0.05)
    assert not second.done(), "第二个请求必须等第一个跑完"

    release.set()
    r1, r2 = await asyncio.gather(first, second)

    assert r1.status_code == r2.status_code == 200
    # 两个请求没有交错
    assert events == [
        ("enter", "问一"), ("exit", "问一"),
        ("enter", "问二"), ("exit", "问二"),
    ]
    # 落库顺序 = 提问顺序，问答配对没串味
    assert [(m["role"], m["content"]) for m in await mapper.list_messages(sid)] == [
        ("user", "问一"), ("assistant", "答:问一"),
        ("user", "问二"), ("assistant", "答:问二"),
    ]


async def test_different_sessions_run_in_parallel(client, mapper, monkeypatch):
    """不同会话必须能并行：做成一把全局锁的话这里会等到超时。"""
    inside = 0
    both_inside = asyncio.Event()

    class _FakeApp:
        async def ainvoke(self, state):
            nonlocal inside
            inside += 1
            if inside == 2:
                both_inside.set()
            # 只等对方进来，不等对方跑完
            await asyncio.wait_for(both_inside.wait(), timeout=2)
            inside -= 1
            return {"final_result": "答"}

    monkeypatch.setattr(agent_api, "get_app", lambda: _FakeApp())

    s1 = (await mapper.create_session(ALICE.id))["id"]
    s2 = (await mapper.create_session(ALICE.id))["id"]

    r1, r2 = await asyncio.gather(
        client.post("/agent/analyse", json={"session_id": s1, "text": "问一"}),
        client.post("/agent/analyse", json={"session_id": s2, "text": "问二"}),
    )

    assert r1.status_code == r2.status_code == 200
    assert inside == 0


async def test_stream_holds_lock_until_the_stream_finishes(client, mapper, monkeypatch):
    """
    流式请求要持锁到流结束。

    锁在 endpoint 函数返回时就释放的话，SSE 一个字节都还没发出去，
    第二个请求就会插进来，同一会话又是并发的。
    """
    events = []
    stream_started = asyncio.Event()
    finish = asyncio.Event()

    async def fake_stream(text, user_id, session_id):
        events.append(("stream-start", text))
        stream_started.set()
        await finish.wait()
        events.append(("stream-end", text))
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_api, "_stream_generator", fake_stream)

    class _FakeApp:
        async def ainvoke(self, state):
            events.append(("agent", state["user_input"]))
            return {"final_result": "答"}

    monkeypatch.setattr(agent_api, "get_app", lambda: _FakeApp())

    sid = (await mapper.create_session(ALICE.id))["id"]

    stream = asyncio.create_task(
        client.post("/agent/analyse/stream", json={"session_id": sid, "text": "问一"})
    )
    await asyncio.wait_for(stream_started.wait(), timeout=2)

    other = asyncio.create_task(
        client.post("/agent/analyse", json={"session_id": sid, "text": "问二"})
    )
    await asyncio.sleep(0.05)
    assert not other.done(), "第二个请求必须等流跑完"

    finish.set()
    r1, r2 = await asyncio.gather(stream, other)

    assert r1.status_code == r2.status_code == 200
    # 第二个请求是在流结束之后才进 Agent 的
    assert events == [
        ("stream-start", "问一"),
        ("stream-end", "问一"),
        ("agent", "问二"),
    ]


async def test_interrupted_stream_releases_the_lock(client, mapper, monkeypatch):
    """流被中断后锁要还回去：否则这个会话被永久卡死，后来的请求全进不来。"""
    started = asyncio.Event()

    async def fake_stream(text, user_id, session_id):
        started.set()
        await asyncio.sleep(30)  # 一直挂着，等着被中断
        yield "data: [DONE]\n\n"  # pragma: no cover

    monkeypatch.setattr(agent_api, "_stream_generator", fake_stream)

    class _FakeApp:
        async def ainvoke(self, state):
            return {"final_result": "答"}

    monkeypatch.setattr(agent_api, "get_app", lambda: _FakeApp())

    sid = (await mapper.create_session(ALICE.id))["id"]

    stream = asyncio.create_task(
        client.post("/agent/analyse/stream", json={"session_id": sid, "text": "问一"})
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    # 客户端断开：请求任务被取消
    stream.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stream

    # 锁必须已经还回去了
    assert agent_api.session_locks.size == 0

    # 后来的请求要能立刻拿到锁
    r = await asyncio.wait_for(
        client.post("/agent/analyse", json={"session_id": sid, "text": "问二"}),
        timeout=3,
    )
    assert r.status_code == 200


async def test_client_disconnect_releases_lock_on_event_loop_thread(mapper, monkeypatch):
    """
    客户端在流到一半断开时，锁必须还回去，而且要还到事件循环线程上。

    这是最容易漏的一条路径：
    - 断开时流停在生成器的 yield 上，而 `async for` 被异常打断**不会关闭生成器**，
      它的 finally 要等 GC 的 asyncgen finalizer 才跑；
    - Starlette 把 `await self.background()` 排在 `stream_response` 之后，
      断连时直接跳过，BackgroundTask 兜底等于没有；
    - 而同步的 background callable 会被丢进线程池，在工作线程里
      `asyncio.Lock.release()` 唤醒不了阻塞在事件循环里的等待者。

    三条都不管的话，这个会话会被永久锁死——用户选的是「排队等待」不是 409，
    后来的请求会无限期挂起。

    这里按原始 ASGI 协议驱动应用（不走 httpx）：uvicorn 报的 spec_version 是 2.3，
    Starlette 走的是任务组分支，断连由 receive 侧的 http.disconnect 触发。
    """
    import threading

    released_on = []
    pool = SessionLockPool()
    original_release = pool.release

    def spy_release(handle):
        released_on.append(threading.current_thread().name)
        original_release(handle)

    monkeypatch.setattr(pool, "release", spy_release)
    monkeypatch.setattr(agent_api, "session_locks", pool)

    async def fake_stream(text, user_id, session_id):
        yield 'data: {"type":"thinking","action":"x","thought":"t"}\n\n'
        await asyncio.sleep(30)  # pragma: no cover
        yield "data: [DONE]\n\n"  # pragma: no cover

    monkeypatch.setattr(agent_api, "_stream_generator", fake_stream)

    app = FastAPI()
    app.include_router(session_router)
    app.include_router(agent_api.agent_router)

    async def current():
        return ALICE

    app.dependency_overrides[get_current_user] = current

    sid = (await mapper.create_session(ALICE.id))["id"]
    body = json.dumps({"session_id": sid, "text": "问一"}).encode()

    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)  # 之后再没有消息
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message):
        if message["type"] == "http.response.body":
            # 模拟客户端写不进去：卡在这里等着被取消
            await asyncio.sleep(30)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "path": "/agent/analyse/stream",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "http_version": "1.1",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.2)  # 让它跑到卡住的地方
    assert pool.size == 1, "请求还在跑，锁应该被持有"

    task.cancel()  # 客户端断开
    await asyncio.gather(task, return_exceptions=True)

    # 锁必须已经还回去：这个会话不能被永久卡死
    assert pool.size == 0, "断连后锁必须还回去"
    assert released_on, "锁根本没被释放过"
    # 释放必须发生在事件循环线程：在工作线程里释放唤醒不了阻塞中的等待者
    assert set(released_on) == {threading.current_thread().name}, released_on


async def test_tool_budget_is_isolated_between_concurrent_requests(client, mapper, monkeypatch):
    """
    并发请求各自的工具预算独立，互不偷额度。

    走真实的图：假 LLM 每轮都要求调 question_set_tool，假工具数自己被调了几次。
    每个请求都应该拿到自己那份完整的预算（上限 3 次），而不是两个请求合起来 3 次。
    """
    from backend.agents.agent import react_agent
    from backend.agents.agent.react_agent import DEFAULT_MAX_TOOL_CALLS

    calls = []
    observed = {}

    class _FakeTool:
        name = "question_set_tool"

        async def _arun(self, query: str):
            calls.append(query)
            return "【题目生成】一道题"

    monkeypatch.setattr(react_agent, "TOOL_MAP", {"question_set_tool": _FakeTool()})

    class _FakeLLM:
        """每轮都要求调同一个工具把预算顶满，同时记下自己看到的预算提示"""

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            system = messages[0].content
            human = json.loads(messages[1].content)
            tag = human["user_input"]
            used = 0
            for line in system.splitlines():
                m = re.search(r"已用(?:满)?\s*(\d+)/", line)
                if m:
                    used = int(m.group(1))
            observed.setdefault(tag, []).append(used)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "thought": "再出一道",
                        "action": "question_set_tool",
                        "action_args": {"query": "x"},
                        "final_result": "",
                    },
                    ensure_ascii=False,
                ),
                tool_calls=[],
                invalid_tool_calls=[],
            )

    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _FakeLLM())

    s1 = (await mapper.create_session(ALICE.id))["id"]
    s2 = (await mapper.create_session(ALICE.id))["id"]

    r1, r2 = await asyncio.gather(
        client.post("/agent/analyse", json={"session_id": s1, "text": "问题A"}),
        client.post("/agent/analyse", json={"session_id": s2, "text": "问题B"}),
    )
    assert r1.status_code == r2.status_code == 200

    # 每个请求的工具都跑满了自己那份上限，没有互相偷额度
    assert len(calls) == DEFAULT_MAX_TOOL_CALLS * 2
    # 两个请求看到的都是自己那条从 0 涨到上限的曲线
    assert observed["问题A"] == observed["问题B"]
    assert observed["问题A"][0] == 0
    assert observed["问题A"][1:4] == [1, 2, 3]
    assert set(observed["问题A"][4:]) <= {DEFAULT_MAX_TOOL_CALLS}
