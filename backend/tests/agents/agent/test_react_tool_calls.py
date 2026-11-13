"""
原生 tool_calls（function calling）路径的测试。

背景：react_think_node 调的是 get_llm().bind_tools(TOOLS)，这等于告诉模型
「可以用原生 function calling」。模型真这么用的时候，content 是空的，
决策全在 message.tool_calls 里。以前只读 content，一次正确的决策会被当成
「模型没说话」，直接落进兜底回答 —— 模型明明做出了正确决策，却被当成失败。

这里覆盖两段：
1. 解析：tool_calls → {thought, action, action_args, final_result}
2. 执行：action → 真的调起工具，参数合法、身份正确、异常不炸图
"""
from types import SimpleNamespace

import pytest

from backend.agents.agent import react_agent


# ---------- 构造假的 LLM 响应 ----------

class _NativeToolCallResponse:
    """模拟模型走原生 function calling：content 为空，决策在 tool_calls 里"""

    def __init__(self, tool_calls, content="", invalid_tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.invalid_tool_calls = invalid_tool_calls or []


def _native(name, args, call_id="call_1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class _FakeLLM:
    """按顺序返回预设的响应，并记录被调用的是同步还是异步入口"""

    def __init__(self, responses, recorder=None):
        self._responses = list(responses)
        self._recorder = recorder if recorder is not None else {}

    def bind_tools(self, tools):
        self._recorder["bound_tools"] = tools
        return self

    def invoke(self, messages):
        self._recorder["sync"] = True
        return self._responses.pop(0)

    async def ainvoke(self, messages):
        self._recorder["async"] = True
        return self._responses.pop(0)


def _state(user_input="解方程 2x+9=5x-3", **over):
    state = {
        "user_input": user_input,
        "user_id": 7,
        "session_id": 3,
        "profile_text": "",
        "thought": "",
        "action": "",
        "action_args": {},
        "messages": [],
        "round": 0,
        "final_result": "",
    }
    state.update(over)
    return state


def _install(monkeypatch, responses):
    recorder = {}
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _FakeLLM(responses, recorder))
    return recorder


# ---------- 一、解析：tool_calls → 决策 ----------

async def test_native_tool_call_is_used_instead_of_fallback(monkeypatch):
    """模型走了原生调用：决策要生效，不能落进「我不能解答用户的问题」。"""
    _install(monkeypatch, [
        _NativeToolCallResponse([_native("common_tool", {"query": "解方程 2x+9=5x-3"})])
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == "common_tool", "原生 tool_calls 里的决策必须被采纳"
    assert out["action_args"] == {"query": "解方程 2x+9=5x-3"}
    assert out["final_result"] == "", "调工具的轮次不能同时给最终回答"
    assert out["round"] == 1


async def test_native_tool_call_without_content_is_not_parse_failure(monkeypatch):
    """content 为空是原生调用的正常形态，不是「模型没说话」。"""
    _install(monkeypatch, [
        _NativeToolCallResponse([_native("extract_tool", {"text": "解方程 2x+9=5x-3"})])
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == "extract_tool"
    assert out["action_args"] == {"text": "解方程 2x+9=5x-3"}
    assert "我不能解答用户的问题" not in out["final_result"]


async def test_native_tool_call_content_still_used_for_thought(monkeypatch):
    """正文里带了推理文字时，thought 要留下来，别浪费。"""
    _install(monkeypatch, [
        _NativeToolCallResponse(
            [_native("common_tool", {"query": "x"})],
            content="学生问的是一元一次方程，先解方程",
        )
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == "common_tool"
    assert "一元一次方程" in out["thought"]


async def test_native_tool_call_content_json_thought_is_extracted(monkeypatch):
    """正文也是我们那套 JSON 时，取其中的 thought。"""
    _install(monkeypatch, [
        _NativeToolCallResponse(
            [_native("common_tool", {"query": "x"})],
            content='{"thought":"想先查记忆","action":"","action_args":{},"final_result":""}',
        )
    ])

    out = await react_agent.react_think_node(_state())

    assert out["thought"] == "想先查记忆"
    assert out["action"] == "common_tool"


async def test_native_tool_call_wins_over_content_json(monkeypatch):
    """
    两边都有决策时以 tool_calls 为准：
    它是结构化的、经过 schema 约束的，比正文里的自由 JSON 更可信。
    """
    _install(monkeypatch, [
        _NativeToolCallResponse(
            [_native("query_memory_tool", {"limit": 3})],
            content='{"thought":"t","action":"common_tool","action_args":{"query":"q"},"final_result":""}',
        )
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == "query_memory_tool"
    assert out["action_args"] == {"limit": 3}


async def test_only_first_native_tool_call_is_taken(monkeypatch):
    """
    图是一次思考对应一次行动。模型一次给多个时只执行第一个，
    剩下的不并发跑（提示词里写了每次只调一个工具）。
    """
    _install(monkeypatch, [
        _NativeToolCallResponse([
            _native("common_tool", {"query": "第一个"}, "call_1"),
            _native("extract_tool", {"text": "第二个"}, "call_2"),
        ])
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == "common_tool"
    assert out["action_args"] == {"query": "第一个"}


async def test_json_content_path_still_works(monkeypatch):
    """没有 tool_calls 时，老的正文 JSON 路径照常走，不能被改坏。"""
    _install(monkeypatch, [
        SimpleNamespace(content='{"thought":"够了","action":"","action_args":{},"final_result":"答案是 x=4"}')
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == ""
    assert out["final_result"] == "答案是 x=4"


async def test_empty_response_still_falls_back(monkeypatch):
    """真的什么都没返回时，仍然走兜底，不能返回空串给用户。"""
    _install(monkeypatch, [SimpleNamespace(content="")])

    out = await react_agent.react_think_node(_state())

    assert out["final_result"] == "我不能解答用户的问题"
    assert out["action"] == ""


async def test_native_tool_call_args_as_json_string(monkeypatch):
    """接口偶尔把 args 传成 JSON 字符串，要能解出来。"""
    _install(monkeypatch, [
        _NativeToolCallResponse([
            {"name": "common_tool", "args": '{"query": "解方程"}', "id": "call_1", "type": "tool_call"}
        ])
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action_args"] == {"query": "解方程"}


async def test_native_tool_call_args_not_a_dict_becomes_empty(monkeypatch):
    """args 不是 dict 时退化成空字典，不能让 **args 展开炸掉。"""
    _install(monkeypatch, [
        _NativeToolCallResponse([
            {"name": "common_tool", "args": None, "id": "call_1", "type": "tool_call"}
        ])
    ])

    out = await react_agent.react_think_node(_state())

    assert out["action"] == "common_tool"
    assert out["action_args"] == {}


# ---------- 二、执行：action → 真的调起工具 ----------

class _RecordingTool:
    """
    假的工具：记录收到的参数，按需抛异常。

    签名跟真实 common_tool 一样是「写死形参」，不用 **kwargs ——
    节点是靠 inspect 读 _arun 的签名来判断哪些参数是必填的，
    **kwargs 会让这个判断失效，测的就不是线上那条路径了。
    """

    def __init__(self, name, result="工具结果", raises=None):
        self.name = name
        self._result = result
        self._raises = raises
        self.calls = []

    async def _arun(self, query: str):
        self.calls.append({"query": query})
        if self._raises:
            raise self._raises
        return self._result


class _ProfileTool(_RecordingTool):
    """画像工具：user_id 由 state 注入，其余可选"""

    async def _arun(self, user_id=None, grade=None, subject=None,
                    weak_points=None, preferences=None, notes=None):
        self.calls.append({
            "user_id": user_id, "grade": grade, "subject": subject,
            "weak_points": weak_points, "preferences": preferences, "notes": notes,
        })
        if self._raises:
            raise self._raises
        return self._result


class _MemoryTool(_RecordingTool):
    """记忆查询工具：user_id / session_id 由 state 注入"""

    async def _arun(self, user_id=None, session_id=None, limit=5):
        self.calls.append({"user_id": user_id, "session_id": session_id, "limit": limit})
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture
def fake_tools(monkeypatch):
    """把 TOOL_MAP 换成可控的假工具"""
    registry: dict = {}

    def register(tool):
        registry[tool.name] = tool
        return tool

    monkeypatch.setattr(react_agent, "TOOL_MAP", registry)
    return register


async def test_tool_exec_runs_tool_with_native_args(fake_tools):
    """原生调用选中的工具要被真的执行，参数原样传进去。"""
    tool = fake_tools(_RecordingTool("common_tool", result="【通用工具】解答如下"))

    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={"query": "解方程"}, round=1)
    )

    assert tool.calls == [{"query": "解方程"}]
    assert "【通用工具】解答如下" in out["messages"][-1].content


async def test_tool_exec_injects_user_id_for_profile_tool(fake_tools):
    """画像工具的 user_id 必须来自 state，模型给的会被覆盖。"""
    tool = fake_tools(_ProfileTool("user_profile_save_tool"))

    await react_agent.tool_exec_node(
        _state(action="user_profile_save_tool",
               action_args={"grade": "七年级", "user_id": 999}, round=1)
    )

    assert tool.calls == [{
        "user_id": 7, "grade": "七年级", "subject": None,
        "weak_points": None, "preferences": None, "notes": None,
    }], "user_id 必须以 state 为准"


async def test_tool_exec_injects_session_ids_for_memory_tool(fake_tools):
    """query_memory_tool 需要两个 ID，都由 state 注入。"""
    tool = fake_tools(_MemoryTool("query_memory_tool"))

    await react_agent.tool_exec_node(
        _state(action="query_memory_tool", action_args={"limit": 3}, round=1)
    )

    assert tool.calls == [{"user_id": 7, "session_id": 3, "limit": 3}]


async def test_tool_exec_drops_unknown_extra_args(fake_tools):
    """
    模型多传参数不能让整轮 500。
    多余的键丢掉（LangChain 走 args_schema 时也是忽略多余字段），
    工具照样跑起来。
    """
    tool = fake_tools(_RecordingTool("common_tool"))

    await react_agent.tool_exec_node(
        _state(action="common_tool",
               action_args={"query": "解方程", "user_id": 999, "foo": "bar"}, round=1)
    )

    assert tool.calls == [{"query": "解方程"}]


async def test_tool_exec_missing_required_arg_becomes_observation(fake_tools):
    """
    必填参数缺失时工具跑不起来。把问题作为 observation 交回给模型，
    它下一轮能改参数 —— 比崩掉好得多。
    """
    tool = fake_tools(_RecordingTool("common_tool"))

    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={}, round=1)
    )

    assert tool.calls == [], "参数不全时不该真的去调工具"
    content = out["messages"][-1].content
    assert "query" in content
    assert "缺少必填参数" in content


async def test_tool_exec_tool_exception_becomes_observation(fake_tools):
    """
    工具抛异常时图不能断：非流式接口会返回 500，流式接口直接断流。
    异常要变成 observation，模型还有机会换个工具或直接回答。
    """
    fake_tools(_RecordingTool("common_tool", raises=RuntimeError("boom")))

    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={"query": "x"}, round=1)
    )

    content = out["messages"][-1].content
    assert "执行出错" in content
    assert "boom" not in content, "异常原文不该发给模型（可能带着 SQL 和参数）"


async def test_tool_exec_unknown_tool_is_reported(fake_tools):
    """模型报了个不存在的工具：作为 observation 返回，让它下一轮改。"""
    out = await react_agent.tool_exec_node(
        _state(action="no_such_tool", action_args={}, round=1)
    )

    assert "未知工具" in out["messages"][-1].content


async def test_tool_exec_does_not_mutate_state_args(fake_tools):
    """注入身份参数时不能就地改写 state 里的 action_args。"""
    fake_tools(_RecordingTool("query_memory_tool"))

    state = _state(action="query_memory_tool", action_args={"limit": 3}, round=1)
    await react_agent.tool_exec_node(state)

    assert state["action_args"] == {"limit": 3}, "state 不该被就地改写"


# ---------- 三、整条链路：原生调用 → 执行 → 继续思考 ----------

async def test_native_tool_call_runs_end_to_end(fake_tools, monkeypatch):
    """
    完整走一轮：模型原生调用工具 → 工具执行 → observation 回到图里。
    工具名要落在 ToolMessage 的 tool_call_id 上，流式接口靠它区分
    skill_loaded 和普通 observation。
    """
    tool = fake_tools(_RecordingTool("common_tool", result="【通用工具】解答如下"))
    _install(monkeypatch, [
        _NativeToolCallResponse([_native("common_tool", {"query": "解方程"})])
    ])

    think = await react_agent.react_think_node(_state())
    exec_out = await react_agent.tool_exec_node(
        _state(action=think["action"], action_args=think["action_args"], round=think["round"])
    )

    assert tool.calls == [{"query": "解方程"}]
    assert exec_out["messages"][-1].tool_call_id == "common_tool_1"
    assert "【通用工具】解答如下" in exec_out["messages"][-1].content


async def test_should_continue_goes_to_tool_after_native_call(fake_tools, monkeypatch):
    """原生调用选出了工具，路由必须继续去执行工具，而不是直接结束。"""
    fake_tools(_RecordingTool("common_tool"))
    _install(monkeypatch, [
        _NativeToolCallResponse([_native("common_tool", {"query": "解方程"})])
    ])

    out = await react_agent.react_think_node(_state())

    assert react_agent.should_continue(out) == "execute_tool"


async def test_should_continue_stops_at_round_limit(fake_tools, monkeypatch):
    """轮次上限：到了 MAX_REACT_ROUNDS 就不再调工具，直接收尾。"""
    fake_tools(_RecordingTool("common_tool"))
    _install(monkeypatch, [
        _NativeToolCallResponse([_native("common_tool", {"query": "解方程"})])
    ])

    out = await react_agent.react_think_node(_state(round=react_agent.MAX_REACT_ROUNDS - 1))

    assert out["round"] == react_agent.MAX_REACT_ROUNDS
    assert react_agent.should_continue(out) == react_agent.END


# ---------- 四、工具签名解析本身 ----------

def test_accepted_params_reads_arun_signature():
    """参数名以 _arun 的签名为准：注入的 user_id 不在 args_schema 里，但在签名里。"""
    from backend.agents.tools import TOOL_MAP

    accepted, required = react_agent._tool_accepted_params(TOOL_MAP["query_memory_tool"])

    assert "user_id" in accepted and "session_id" in accepted
    assert "limit" in accepted
    assert required == set(), "这些参数都有默认值，不是必填"


def test_accepted_params_marks_required_without_default():
    """没有默认值的参数是必填。"""
    from backend.agents.tools import TOOL_MAP

    accepted, required = react_agent._tool_accepted_params(TOOL_MAP["common_tool"])

    assert accepted == {"query"}
    assert required == {"query"}


def test_accepted_params_returns_none_for_kwargs_tool():
    """
    **kwargs 兜底的工具读不出具体参数名：accepted 返回 None，意思是「别筛」。
    这一支由执行节点后面的 try/except 兜底。
    """
    class _KwargsTool:
        async def _arun(self, **kwargs):
            return "ok"

    accepted, required = react_agent._tool_accepted_params(_KwargsTool())

    assert accepted is None
    assert required == set()


def test_accepted_params_returns_none_when_signature_unreadable():
    """签名读不出来时同样返回 None（不筛参数），而不是空集合。"""
    class _BrokenTool:
        async def _arun(self, query):
            return "ok"

    tool = _BrokenTool()
    # 把签名换成读不出来的形式：inspect 会对它抛 ValueError
    tool._arun.__func__.__signature__ = "not-a-signature"

    accepted, required = react_agent._tool_accepted_params(tool)

    assert accepted is None
    assert required == set()
