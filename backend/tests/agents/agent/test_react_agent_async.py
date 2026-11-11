import inspect
from types import SimpleNamespace

from backend.agents.agent import react_agent


def _state(user_input="解方程 2x+9=5x-3"):
    return {
        "user_input": user_input,
        "user_id": 1,
        "session_id": 1,
        "thought": "",
        "action": "",
        "action_args": {},
        "messages": [],
        "round": 0,
        "final_result": "",
    }

class _FakeLLM:
    """记录被调用的是同步还是异步入口"""

    def __init__(self,content:str,recorder:dict):
        self._content = content
        self._recorder = recorder

    def bind_tools(self,tools):
        return self

    def invoke(self,messages):
        self._recorder["sync"] = True
        return SimpleNamespace(content=self._content)

    async def ainvoke(self,messages):
        self._recorder["async"] = True
        return SimpleNamespace(content=self._content)

def test_react_think_node_is_coroutine_function():
    assert inspect.iscoroutinefunction(react_agent.react_think_node),(
        "react_think_node 必须是 async，否则 LangGraph 会把它丢进默认线程池，"
        "和向量库操作抢同一个池"
    )

async def test_react_think_node_uses_ainvoke(monkeypatch):
    recorder = {}
    content = '{"thought":"够了","action":"","action_args":{},"final_result":"答案是 x=4"}'
    monkeypatch.setattr(
        react_agent,"get_llm",lambda *a,**kw: _FakeLLM(content,recorder)
    )

    out = await react_agent.react_think_node(_state())
    assert recorder == {'async':True},'不允许走同步 invoke'
    assert out['final_result'] == "答案是 x=4"
    assert out['action'] == ""
    assert out["round"] == 1


# ---------- 长期画像注入 system prompt ----------

def _profile(**kw):
    base = dict(grade="七年级", subject="数学", weak_points={}, preferences={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_profile_section_absent_when_no_profile():
    """没有画像时不能留下空的「学生画像」标题，否则模型会对着空段落瞎猜。"""
    prompt = react_agent.build_react_system_prompt(None, "")
    assert "学生画像" not in prompt


def test_profile_section_present_when_given():
    """有画像时必须进 system prompt，并声明它只是背景、不是本次提问。"""
    prompt = react_agent.build_react_system_prompt(None, "年级：七年级 | 学科：数学")
    assert "# 学生画像" in prompt
    assert "七年级" in prompt
    assert "不要直接复述" in prompt


def test_format_profile_handles_none():
    """新用户没有画像：必须得到空串，调用方才能据此省掉整段。"""
    assert react_agent.format_profile(None) == ""


def test_format_profile_renders_fields_with_mastery():
    """weak_points 的值是掌握程度；只列键名会把「已掌握」的点也标成薄弱。"""
    text = react_agent.format_profile(_profile(
        weak_points={"因式分解": "已掌握", "函数": "薄弱"},
        preferences={"题目风格": "不要与原题雷同"},
    ))
    assert "年级：七年级 | 学科：数学" in text
    assert "知识点掌握：因式分解=已掌握、函数=薄弱" in text
    assert "长期偏好：题目风格=不要与原题雷同" in text
    assert "薄弱知识点" not in text, "不能把全部知识点笼统标成薄弱"


def test_format_profile_omits_empty_lines():
    """字典为空就整行省掉，不往每轮 prompt 里塞空标签。"""
    text = react_agent.format_profile(_profile())
    assert text == "年级：七年级 | 学科：数学"


def test_format_profile_unknown_for_blank_grade_subject():
    """年级、学科缺一个时用「未知」占位，让模型知道是没填而不是没读到。"""
    text = react_agent.format_profile(_profile(grade="", weak_points={"函数": "薄弱"}))
    assert text.splitlines()[0] == "年级：未知 | 学科：数学"


def test_format_profile_excludes_notes():
    """notes 是自由文本、最多 50 条，注入会撑大每轮输入；只在主动查询时返回。"""
    text = react_agent.format_profile(_profile(notes=["做题时喜欢先看思路", "晚上效率更高"]))
    assert "先看思路" not in text
    assert "晚上效率" not in text


def test_format_profile_all_empty_returns_blank():
    """画像存在但什么都没填：返回空串，不输出「年级：未知 | 学科：未知」这种无信息段。"""
    assert react_agent.format_profile(_profile(grade="", subject="")) == ""
    assert react_agent.format_profile(
        _profile(grade="", subject="", weak_points=None, preferences=None)
    ) == ""


def test_format_profile_accepts_pydantic_response():
    """线上传进来的是 UserProfileResponse，必须和 SimpleNamespace 一样能格式化且不带 notes。"""
    from backend.schemas.response.user_profile_response import UserProfileResponse

    profile = UserProfileResponse(
        user_id=1, grade="八年级", subject="数学",
        weak_points={"因式分解": "已掌握", "函数": "薄弱"},
        preferences={"难度倾向": "循序渐进"},
        notes=["不该出现的自由文本"],
    )
    text = react_agent.format_profile(profile)
    assert "年级：八年级 | 学科：数学" in text
    assert "因式分解=已掌握" in text and "函数=薄弱" in text
    assert "难度倾向=循序渐进" in text
    assert "自由文本" not in text


class _RecorderLLM(_FakeLLM):
    """记下送进模型的 system / human 两条消息"""

    def __init__(self, content, captured):
        super().__init__(content, {})
        self._captured = captured

    async def ainvoke(self, messages):
        self._captured["system"] = messages[0].content
        self._captured["human"] = messages[1].content
        return await super().ainvoke(messages)


_DONE = '{"thought":"t","action":"","action_args":{},"final_result":"ok"}'


async def test_react_think_node_passes_profile_into_prompt(monkeypatch):
    """画像是背景不是诉求：进 system，混进 human 会被当成本次提问的一部分。"""
    captured = {}
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _RecorderLLM(_DONE, captured))

    state = _state()
    state["profile_text"] = "年级：八年级 | 学科：数学"
    await react_agent.react_think_node(state)

    assert "八年级" in captured["system"]
    assert "八年级" not in captured["human"]


async def test_react_think_node_without_profile_text_key(monkeypatch):
    """旧调用方构造的 state 没有 profile_text 键，节点不能 KeyError。"""
    captured = {}
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _RecorderLLM(_DONE, captured))

    out = await react_agent.react_think_node(_state())
    assert out["final_result"] == "ok"
    assert "学生画像" not in captured["system"]
