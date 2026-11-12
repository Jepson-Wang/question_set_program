"""GraphState 的构建只有一处：analyse 和流式接口都走 _build_state，画像不会只在其中一个生效。"""
import json
from types import SimpleNamespace

from backend.api.user_api import agent_api

PROFILE = SimpleNamespace(
    grade="七年级", subject="数学",
    weak_points={"函数": "薄弱"}, preferences={"题目风格": "不要与原题雷同"},
    notes=["私密观察记录"],
)
RECENT = [{"memory": {"user_memory": "上一问", "model_memory": "上一答"}}]


def _planner_data(**over):
    data = {"short_memory": RECENT, "long_memory": PROFILE, "session_digest": "要点摘要"}
    data.update(over)
    return data


def _patch_memory(monkeypatch, data):
    async def fake(user_id, session_id):
        return data
    monkeypatch.setattr(agent_api.memory_manager, "get_memory_for_planner", fake)


class _FakeApp:
    """记录收到的初始 state，直接给出终态"""

    def __init__(self):
        self.state = None

    async def ainvoke(self, state):
        self.state = state
        return {"final_result": "答"}

    async def astream(self, state, stream_mode=None):
        self.state = state
        yield {"react_think": {"action": "", "thought": "", "final_result": "答"}}


async def test_build_state_includes_profile_text(monkeypatch):
    """画像格式化后放进 state，节点才能在 system prompt 里用上。"""
    _patch_memory(monkeypatch, _planner_data())
    state = await agent_api._build_state("现在的问题", 1, 2)
    assert "年级：七年级 | 学科：数学" in state["profile_text"]
    assert "函数=薄弱" in state["profile_text"]
    assert "私密观察记录" not in state["profile_text"]
    assert (state["user_id"], state["session_id"], state["round"]) == (1, 2, 0)


async def test_build_state_user_input_format_unchanged_and_profile_free(monkeypatch):
    """user_input 沿用原格式；画像进了它就会被当成本次诉求。"""
    _patch_memory(monkeypatch, _planner_data())
    state = await agent_api._build_state("现在的问题", 1, 2)
    expected_context = agent_api._format_memory_context(RECENT, "要点摘要")
    assert state["user_input"] == f"{expected_context}\n\n【当前问题】\n现在的问题"
    assert "七年级" not in state["user_input"] and "函数=薄弱" not in state["user_input"]


async def test_build_state_without_memory_or_profile(monkeypatch):
    """新用户没有记忆也没有画像：user_input 就是原文，profile_text 为空串。"""
    _patch_memory(monkeypatch, {"short_memory": [], "long_memory": None, "session_digest": ""})
    state = await agent_api._build_state("你好", 1, 2)
    assert state["user_input"] == "你好"
    assert state["profile_text"] == ""


async def test_both_endpoints_use_build_state(monkeypatch):
    """两个端点各自拼 state 时漏改一个，画像就只在一条路径上生效。"""
    _patch_memory(monkeypatch, _planner_data())
    seen = []

    app = _FakeApp()
    monkeypatch.setattr(agent_api, "get_app", lambda: app)

    async def noop(*a, **kw):
        return None

    chat = SimpleNamespace(add_message=noop)
    monkeypatch.setattr(agent_api, "get_chat_mapper", lambda: chat)
    monkeypatch.setattr(agent_api, "require_own_session", noop)
    monkeypatch.setattr(agent_api.memory_manager, "add_memory", noop)

    real_build = agent_api._build_state

    async def spy(*args, **kwargs):
        seen.append(args)
        return await real_build(*args, **kwargs)
    monkeypatch.setattr(agent_api, "_build_state", spy)

    request = agent_api.TextRequest(session_id=2, text="现在的问题")
    await agent_api.analyse(request, SimpleNamespace(id=1))
    assert len(seen) == 1
    assert "七年级" in app.state["profile_text"]

    app.state = None
    events = [e async for e in agent_api._stream_generator("现在的问题", 1, 2)]
    assert len(seen) == 2
    assert "七年级" in app.state["profile_text"]
    assert json.loads(events[0][len("data: "):])["type"] == "result"
