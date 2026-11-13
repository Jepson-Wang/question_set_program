"""
工具调用预算的测试。

背景：同一次请求里，Agent 可能反复调同一个工具 —— 最典型的是
question_set_agent，生成结果不合预期就再出一道。没有上限时，一次请求可能
把同一个工具跑满所有轮次，白烧 token 还把用户等在那儿。

这里覆盖三段：
1. 预算本身：每个工具的次数上限、轮次上限
2. 执行侧：超限的工具不执行，计数正确累加
3. 规划侧：预算写进 system prompt，模型能提前知道自己还剩多少
"""
from types import SimpleNamespace

import pytest

from backend.agents.agent import react_agent
from backend.agents.agent.react_agent import (
    DEFAULT_MAX_TOOL_CALLS,
    MAX_REACT_ROUNDS,
    TOOL_MAX_CALLS,
    _final_answer_from_observations,
    format_tool_budget,
    get_max_tool_calls,
)


# ---------- 假工具 ----------

class _RecordingTool:
    """
    假的工具：记录收到的参数。

    签名跟真实 common_tool 一样是「写死形参」，不用 **kwargs ——
    节点是靠 inspect 读 _arun 的签名来判断哪些参数是必填的，
    **kwargs 会让这个判断失效，测的就不是线上那条路径了。
    """

    def __init__(self, name, result="工具结果"):
        self.name = name
        self._result = result
        self.calls = []

    async def _arun(self, query: str):
        self.calls.append({"query": query})
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
        "tool_call_counts": {},
    }
    state.update(over)
    return state


# ---------- 一、预算常量本身 ----------

def test_default_max_tool_calls_is_three():
    """未单独配置的工具，上限是三次。"""
    assert DEFAULT_MAX_TOOL_CALLS == 3


def test_max_react_rounds_is_eight():
    """轮次上限从 5 提到 8。"""
    assert MAX_REACT_ROUNDS == 8


def test_get_max_tool_calls_uses_default_for_unlisted_tool():
    """没在表里的工具走默认值。"""
    assert get_max_tool_calls("common_tool") == DEFAULT_MAX_TOOL_CALLS


def test_get_max_tool_calls_uses_override_for_listed_tool():
    """表里配了的工具用自己的值，且这个值确实不同于默认值。"""
    assert "question_set_tool" in TOOL_MAX_CALLS
    assert get_max_tool_calls("question_set_tool") == TOOL_MAX_CALLS["question_set_tool"]
    # 配置项要和默认值区分开，否则这张表就是个摆设
    assert TOOL_MAX_CALLS["question_set_tool"] == DEFAULT_MAX_TOOL_CALLS


def test_get_max_tool_calls_reflects_table_changes(monkeypatch):
    """改表里的值就生效，不用改代码。"""
    monkeypatch.setitem(react_agent.TOOL_MAX_CALLS, "question_set_tool", 1)
    assert get_max_tool_calls("question_set_tool") == 1


# ---------- 二、执行侧：超限不执行 ----------

async def test_tool_exec_counts_calls(fake_tools):
    """执行一次就记一次数，计数写进 state。"""
    fake_tools(_RecordingTool("common_tool"))

    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={"query": "x"}, round=1)
    )

    assert out["tool_call_counts"] == {"common_tool": 1}


async def test_tool_exec_increments_count_across_rounds(fake_tools):
    """连着调同一个工具，计数累加。"""
    tool = fake_tools(_RecordingTool("common_tool"))

    state = _state(action="common_tool", action_args={"query": "x"}, round=1)
    out = await react_agent.tool_exec_node(state)
    out2 = await react_agent.tool_exec_node(
        {**state, "round": 2, "tool_call_counts": out["tool_call_counts"]}
    )

    assert out2["tool_call_counts"] == {"common_tool": 2}
    assert len(tool.calls) == 2


async def test_tool_exec_blocks_after_limit(fake_tools):
    """
    超过上限后不再执行工具。
    背景就是这个任务：模型对同一个工具反复调用，白烧 token。
    """
    tool = fake_tools(_RecordingTool("question_set_tool", result="【题目生成】题目"))

    counts = {"question_set_tool": DEFAULT_MAX_TOOL_CALLS}
    out = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=1, tool_call_counts=counts)
    )

    assert tool.calls == [], "超过上限后不该再执行工具"
    content = out["messages"][-1].content
    assert "【工具预算】" in content
    assert "question_set_tool" in content


async def test_tool_exec_allows_up_to_limit(fake_tools):
    """正好用满上限的那一次是要执行的。"""
    tool = fake_tools(_RecordingTool("question_set_tool"))

    counts = {"question_set_tool": DEFAULT_MAX_TOOL_CALLS - 1}
    out = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=1, tool_call_counts=counts)
    )

    assert len(tool.calls) == 1
    assert out["tool_call_counts"] == {"question_set_tool": DEFAULT_MAX_TOOL_CALLS}


async def test_blocked_call_does_not_inflate_count(fake_tools):
    """被拦下的调用不该继续加数，否则计数会失真。"""
    fake_tools(_RecordingTool("question_set_tool"))

    counts = {"question_set_tool": DEFAULT_MAX_TOOL_CALLS}
    out = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=1, tool_call_counts=counts)
    )

    assert out["tool_call_counts"] == {"question_set_tool": DEFAULT_MAX_TOOL_CALLS}


async def test_limit_is_per_tool_not_global(fake_tools):
    """一个工具用满预算，不影响另一个工具。"""
    qst = fake_tools(_RecordingTool("question_set_tool"))
    common = fake_tools(_RecordingTool("common_tool"))

    counts = {"question_set_tool": DEFAULT_MAX_TOOL_CALLS}
    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={"query": "x"},
               round=1, tool_call_counts=counts)
    )

    assert len(common.calls) == 1, "别的工具不该被牵连"
    assert qst.calls == []
    assert out["tool_call_counts"] == {
        "question_set_tool": DEFAULT_MAX_TOOL_CALLS,
        "common_tool": 1,
    }


async def test_counts_are_per_request_not_global(fake_tools):
    """
    计数只属于本次请求。两次独立请求各自从零开始，
    否则用户问第二遍同样的题时会被上一次的预算挡住。
    """
    tool = fake_tools(_RecordingTool("question_set_tool"))

    first = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=1, tool_call_counts={})
    )
    # 第二次请求：state 是全新的，计数从零开始
    second = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=1, tool_call_counts={})
    )

    assert first["tool_call_counts"] == {"question_set_tool": 1}
    assert second["tool_call_counts"] == {"question_set_tool": 1}
    assert len(tool.calls) == 2


async def test_unknown_tool_is_not_counted(fake_tools):
    """未知工具压根没执行，不该占预算。"""
    out = await react_agent.tool_exec_node(
        _state(action="no_such_tool", action_args={}, round=1, tool_call_counts={})
    )

    assert "未知工具" in out["messages"][-1].content
    assert out["tool_call_counts"] == {}


async def test_budget_observation_is_recorded_in_messages(fake_tools):
    """被拦下的那次也要留一条 observation，模型下一轮才知道发生了什么。"""
    fake_tools(_RecordingTool("question_set_tool"))

    out = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=1, tool_call_counts={"question_set_tool": DEFAULT_MAX_TOOL_CALLS})
    )

    assert len(out["messages"]) == 1
    assert out["messages"][-1].tool_call_id == "question_set_tool_1"


# ---------- 三、规划侧：预算写进 prompt ----------

def test_budget_prompt_lists_remaining_calls():
    """已用过的工具要写明还剩几次，模型才能自己规划。"""
    text = format_tool_budget({"question_set_tool": 1}, current_round=2)

    assert "question_set_tool" in text
    assert "已用 1/3 次" in text
    assert "还可调用 2 次" in text


def test_budget_prompt_marks_exhausted_tool():
    """用满的工具要明确说「不要再调用它」。"""
    text = format_tool_budget({"question_set_tool": 3}, current_round=4)

    assert "已用满 3/3 次" in text
    assert "不要再调用它" in text


def test_budget_prompt_reports_remaining_rounds():
    """剩余轮次要写出来，模型才知道该不该再多调一次。"""
    text = format_tool_budget({}, current_round=3)

    assert f"最多 {MAX_REACT_ROUNDS} 轮" in text
    assert "当前是第 3 轮" in text
    assert f"还剩 {MAX_REACT_ROUNDS - 3} 轮" in text


def test_budget_prompt_warns_on_last_round():
    """最后一轮要明确说「直接给最终回答」，别再调工具。"""
    text = format_tool_budget({}, current_round=MAX_REACT_ROUNDS)

    assert "这是最后一轮" in text
    assert "请直接给出最终回答" in text


def test_budget_prompt_always_states_default_limit():
    """默认上限要写出来，模型才知道没列出的工具能调几次。"""
    text = format_tool_budget({}, current_round=1)

    assert f"默认上限 {DEFAULT_MAX_TOOL_CALLS} 次" in text


def test_budget_prompt_handles_none_counts():
    """没有计数记录时也要能生成文本（第一轮就是这种情况）。"""
    text = format_tool_budget(None, current_round=1)

    assert "默认上限" in text
    assert "第 1 轮" in text


def test_system_prompt_contains_budget_section():
    """预算段要真的进 system prompt，否则模型看不到。"""
    prompt = react_agent.build_react_system_prompt(
        None, "", format_tool_budget({"question_set_tool": 1}, 2)
    )

    assert "# 调用预算" in prompt
    assert "question_set_tool" in prompt


def test_system_prompt_without_budget_has_no_budget_section():
    """没有预算时不要留个空标题，模型会对着空段落瞎猜。"""
    prompt = react_agent.build_react_system_prompt(None, "")

    assert "# 调用预算" not in prompt


# ---------- 四、轮次上限 ----------

def test_should_continue_goes_to_tool_before_limit():
    """未到轮次上限时继续调工具。"""
    assert react_agent.should_continue(_state(action="common_tool", round=1)) == "execute_tool"
    assert react_agent.should_continue(
        _state(action="common_tool", round=MAX_REACT_ROUNDS - 1)
    ) == "execute_tool"


def test_should_continue_stops_at_round_limit():
    """到了轮次上限就不再调工具。"""
    assert react_agent.should_continue(
        _state(action="common_tool", round=MAX_REACT_ROUNDS)
    ) == react_agent.END


async def test_last_round_with_pending_tool_produces_answer(monkeypatch):
    """
    最后一轮模型还想调工具：不能再调了，但也不能让用户收到空回答。
    要把已经拿到的观察整理成最终回答。
    """
    from langchain_core.messages import ToolMessage

    monkeypatch.setattr(
        react_agent, "get_llm",
        lambda *a, **kw: SimpleNamespace(
            bind_tools=lambda t: SimpleNamespace(
                ainvoke=lambda m: _async_msg(
                    '{"thought":"还想再出题","action":"question_set_tool",'
                    '"action_args":{"query":"x"},"final_result":""}'
                )
            )
        )
    )

    messages = [ToolMessage(content="【题目生成】第一道题", tool_call_id="question_set_tool_1")]
    out = await react_agent.react_think_node(
        _state(action="", round=MAX_REACT_ROUNDS - 1, messages=messages)
    )

    assert out["action"] == "", "最后一轮不该再请求调工具"
    assert out["final_result"] == "【题目生成】第一道题"
    assert out["round"] == MAX_REACT_ROUNDS


async def test_last_round_without_observations_still_answers(monkeypatch):
    """最后一轮且什么都没观察到：也要给一句提示，不能是空串。"""
    monkeypatch.setattr(
        react_agent, "get_llm",
        lambda *a, **kw: SimpleNamespace(
            bind_tools=lambda t: SimpleNamespace(
                ainvoke=lambda m: _async_msg(
                    '{"thought":"还想再出题","action":"question_set_tool",'
                    '"action_args":{"query":"x"},"final_result":""}'
                )
            )
        )
    )

    out = await react_agent.react_think_node(
        _state(action="", round=MAX_REACT_ROUNDS - 1, messages=[])
    )

    assert out["final_result"]
    assert out["action"] == ""


async def test_last_round_keeps_model_final_result(monkeypatch):
    """模型最后一轮直接给出了答案（action 为空）：原样保留，不被覆盖。"""
    monkeypatch.setattr(
        react_agent, "get_llm",
        lambda *a, **kw: SimpleNamespace(
            bind_tools=lambda t: SimpleNamespace(
                ainvoke=lambda m: _async_msg(
                    '{"thought":"够了","action":"","action_args":{},"final_result":"最终答案"}'
                )
            )
        )
    )

    out = await react_agent.react_think_node(
        _state(action="", round=MAX_REACT_ROUNDS - 1, messages=[])
    )

    assert out["final_result"] == "最终答案"


async def test_last_round_prefers_model_answer_over_observation(monkeypatch):
    """
    模型最后一轮同时给了 action 和 final_result：以它自己写的答案为准。
    观察拼出来的只是兜底，不能盖掉模型写的。
    """
    from langchain_core.messages import ToolMessage

    monkeypatch.setattr(
        react_agent, "get_llm",
        lambda *a, **kw: SimpleNamespace(
            bind_tools=lambda t: SimpleNamespace(
                ainvoke=lambda m: _async_msg(
                    '{"thought":"还想再出题，但先给个答案","action":"question_set_tool",'
                    '"action_args":{"query":"x"},"final_result":"模型自己写的答案"}'
                )
            )
        )
    )

    messages = [ToolMessage(content="【题目生成】第 1 道题", tool_call_id="q_1")]
    out = await react_agent.react_think_node(
        _state(action="", round=MAX_REACT_ROUNDS - 1, messages=messages)
    )

    assert out["final_result"] == "模型自己写的答案"
    assert out["action"] == ""


def test_final_answer_prefers_real_tool_result():
    """最终回答优先用真正的工具结果，不是系统的「预算用完」提示。"""
    from langchain_core.messages import ToolMessage

    messages = [
        ToolMessage(content="【题目生成】题目", tool_call_id="q_1"),
        ToolMessage(content="【工具预算】question_set_tool 已经调用 3 次", tool_call_id="q_2"),
    ]

    assert _final_answer_from_observations(messages) == "【题目生成】题目"


def test_final_answer_does_not_leak_internal_notice():
    """
    全是内部提示时，不能把「请不要再调用它」这种写给模型的指令发给用户。

    「工具预算」「工具执行」是系统跟模型之间的话。用户看到的是
    「本次没有得到有效结果，换一种问法再试」，不是后台日志。
    """
    from langchain_core.messages import ToolMessage

    messages = [
        ToolMessage(
            content="【工具预算】common_tool 在本次请求中已经调用 3 次，达到上限 3 次，"
                    "不再执行。请不要再调用它，改用其他工具，或者基于已有信息直接给出最终回答。",
            tool_call_id="common_tool_3",
        ),
    ]

    result = _final_answer_from_observations(messages)

    assert result, "不能是空串"
    assert "【工具预算】" not in result
    assert "请不要再调用它" not in result
    assert "换一种问法" in result


def test_final_answer_mentions_failing_tool():
    """全部失败时要说清是哪个工具出的问题，用户才知道该换什么。"""
    from langchain_core.messages import ToolMessage

    messages = [
        ToolMessage(
            content="【工具执行】common_tool 执行出错，本次没有拿到结果。请换个方式再试一次。",
            tool_call_id="common_tool_2",
        ),
    ]

    result = _final_answer_from_observations(messages)

    assert "common_tool" in result
    assert "【工具执行】" not in result


def test_final_answer_with_no_messages():
    """没有任何观察时给一句兜底说明。"""
    assert _final_answer_from_observations([])


# ---------- 五、state 里带上计数 ----------

async def test_build_state_initializes_tool_call_counts(monkeypatch):
    """
    初始 state 要带上 tool_call_counts。
    初始 state 只有 _build_state 一处构造（analyse 和流式接口共用），
    这里漏加就等于没有上限。
    """
    from backend.api.user_api import agent_api

    async def fake(user_id, session_id):
        return {"short_memory": [], "long_memory": None, "session_digest": ""}

    monkeypatch.setattr(agent_api.memory_manager, "get_memory_for_planner", fake)

    state = await agent_api._build_state("问题", 1, 2)

    assert state["tool_call_counts"] == {}


async def test_build_state_counts_reset_per_request(monkeypatch):
    """两次请求的计数互相独立。"""
    from backend.api.user_api import agent_api

    async def fake(user_id, session_id):
        return {"short_memory": [], "long_memory": None, "session_digest": ""}

    monkeypatch.setattr(agent_api.memory_manager, "get_memory_for_planner", fake)

    first = await agent_api._build_state("问题", 1, 2)
    first["tool_call_counts"]["question_set_tool"] = 3
    second = await agent_api._build_state("问题", 1, 2)

    assert second["tool_call_counts"] == {}


def test_graph_state_declares_tool_call_counts():
    """GraphState 里要声明这个键，否则图的状态里没有它的位置。"""
    from backend.agents.agent.tools import GraphState

    assert "tool_call_counts" in GraphState.__annotations__


# ---------- 六、真实编译后的图里计数要跨轮保留 ----------

class _AlwaysSameToolLLM:
    """每一轮都请求同一个工具，用来把预算顶满"""

    def __init__(self):
        self.n = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.n += 1
        return SimpleNamespace(
            content=('{"thought":"再出一道","action":"question_set_tool",'
                     '"action_args":{"query":"x"},"final_result":""}'),
            tool_calls=[],
            invalid_tool_calls=[],
        )


class _CountingQST:
    """数自己被调了几次的假 question_set_tool"""

    name = "question_set_tool"

    def __init__(self):
        self.calls = 0

    async def _arun(self, query: str):
        self.calls += 1
        return f"【题目生成】第 {self.calls} 道题"


async def test_counts_survive_across_rounds_in_real_graph(fake_tools, monkeypatch):
    """
    跑真的编译图，验证计数跨轮保留。

    这是整套功能成立的前提：tool_call_counts 在 GraphState 里没有配 reducer，
    LangGraph 对无 reducer 的键默认是「后写覆盖」。react_think_node 的返回值里
    没有这个键 —— 如果 LangGraph 把「节点没返回的键」当成清空，计数每轮都会被
    重置，上限等于没做。单测直接调节点验证不到这一点，必须过真的图。
    """
    tool = fake_tools(_CountingQST())
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _AlwaysSameToolLLM())

    app = react_agent.get_app()
    result = await app.ainvoke(
        _state(user_input="给我出几道题", tool_call_counts={})
    )

    # 工具最多真跑 3 次，第 4 次起被拦
    assert tool.calls == DEFAULT_MAX_TOOL_CALLS
    assert result["tool_call_counts"] == {"question_set_tool": DEFAULT_MAX_TOOL_CALLS}


async def test_real_graph_gives_non_empty_final_answer(fake_tools, monkeypatch):
    """
    跑真的图：预算顶满后，最终回答不能是空串。
    用户等了半天，看到一片空白是最糟的结果。
    """
    tool = fake_tools(_CountingQST())
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _AlwaysSameToolLLM())

    app = react_agent.get_app()
    result = await app.ainvoke(
        _state(user_input="给我出几道题", tool_call_counts={})
    )

    assert result["final_result"], "最终回答不能为空"
    assert "题目生成" in result["final_result"]
    # 预算顶满后模型不该再真的调工具
    assert tool.calls == DEFAULT_MAX_TOOL_CALLS


async def test_real_graph_respects_round_limit(fake_tools, monkeypatch):
    """
    跑真的图：轮次上限是 MAX_REACT_ROUNDS，不能超过。
    模型一直要调工具时，思考轮数封顶。
    """
    fake_tools(_CountingQST())
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: _AlwaysSameToolLLM())

    app = react_agent.get_app()
    result = await app.ainvoke(
        _state(user_input="给我出几道题", tool_call_counts={})
    )

    assert result["round"] == MAX_REACT_ROUNDS


# ---------- 七、prompt 里的额度信息要准确 ----------

def test_budget_prompt_reports_non_default_override(monkeypatch):
    """
    给某个工具配了跟默认不同的额度时，prompt 要单独说出来。

    只写「默认上限 3 次」，模型会以为所有工具都一个数——那个配了 5 次的工具
    它就只会用 3 次，额度白给了。
    """
    monkeypatch.setitem(react_agent.TOOL_MAX_CALLS, "question_set_tool", 5)

    text = format_tool_budget({}, current_round=1)

    assert "question_set_tool 最多 5 次" in text


def test_budget_prompt_omits_override_note_when_same_as_default(monkeypatch):
    """额度跟默认一样时不提「例外」，避免给模型多余的噪音。"""
    monkeypatch.setitem(react_agent.TOOL_MAX_CALLS, "question_set_tool", DEFAULT_MAX_TOOL_CALLS)

    text = format_tool_budget({}, current_round=1)

    assert "额度与默认不同的工具" not in text


def test_budget_prompt_shows_real_limit_for_used_tool(monkeypatch):
    """已经用过的工具，剩余次数要按它自己的上限算，不是默认值。"""
    monkeypatch.setitem(react_agent.TOOL_MAX_CALLS, "question_set_tool", 5)

    text = format_tool_budget({"question_set_tool": 1}, current_round=2)

    assert "已用 1/5 次" in text
    assert "还可调用 4 次" in text


# ---------- 八、兜底答案不能是 Skill 剧本 ----------

def test_final_answer_skips_skill_observation():
    """
    load_skill_tool 返回的是给模型看的剧本，不能当成给用户的答案。

    剧本是几百行 Markdown 指令，直接甩给用户等于没回答。
    """
    from langchain_core.messages import ToolMessage

    messages = [
        ToolMessage(content="# 剧本：\n1. 先做这个", tool_call_id="load_skill_tool_1"),
        ToolMessage(content="【题目生成】第 1 道题", tool_call_id="question_set_tool_2"),
    ]

    assert _final_answer_from_observations(messages) == "【题目生成】第 1 道题"


def test_final_answer_never_returns_skill_script_alone():
    """只加载了剧本、没别的结果时，也不能把剧本当答案。"""
    from langchain_core.messages import ToolMessage

    messages = [
        ToolMessage(content="# 剧本：\n1. 先做这个", tool_call_id="load_skill_tool_1"),
    ]

    result = _final_answer_from_observations(messages)

    assert "剧本" not in result
    assert result


def test_observation_tool_name_extracted_from_tool_call_id():
    """tool_call_id 是「工具名_轮次」，取工具名要能正确切掉轮次。"""
    from langchain_core.messages import ToolMessage

    msg = ToolMessage(content="x", tool_call_id="load_skill_tool_3")

    assert react_agent._observation_tool_name(msg) == "load_skill_tool"
    assert react_agent._observation_tool_name(
        ToolMessage(content="x", tool_call_id="question_set_tool_12")
    ) == "question_set_tool"


# ---------- 九、额度覆盖要真的作用到执行器 ----------

async def test_override_limit_applies_to_executor(monkeypatch, fake_tools):
    """
    给工具配了更大的额度，执行器要真的放行那么多次。

    这条断言盯的是「TOOL_MAX_CALLS 改了之后行为跟着变」——只查表里的数字
    是查不出配置有没有接上执行器的。
    """
    monkeypatch.setitem(react_agent.TOOL_MAX_CALLS, "question_set_tool", 5)
    tool = fake_tools(_RecordingTool("question_set_tool"))

    counts = {}
    for r in range(1, 6):
        out = await react_agent.tool_exec_node(
            _state(action="question_set_tool", action_args={"query": "x"},
                   round=r, tool_call_counts=counts)
        )
        counts = out["tool_call_counts"]

    assert len(tool.calls) == 5
    assert counts["question_set_tool"] == 5


async def test_override_limit_blocks_at_its_own_value(monkeypatch, fake_tools):
    """额度调到 5 次，就该在第 6 次拦下，而不是按默认的 3 次拦。"""
    monkeypatch.setitem(react_agent.TOOL_MAX_CALLS, "question_set_tool", 5)
    tool = fake_tools(_RecordingTool("question_set_tool"))

    out = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=6, tool_call_counts={"question_set_tool": 4})
    )

    assert len(tool.calls) == 1, "还剩一次额度，应当放行"
    assert out["tool_call_counts"]["question_set_tool"] == 5

    blocked = await react_agent.tool_exec_node(
        _state(action="question_set_tool", action_args={"query": "x"},
               round=7, tool_call_counts={"question_set_tool": 5})
    )
    assert len(tool.calls) == 1, "额度用完后不该再执行"
    assert "【工具预算】" in blocked["messages"][-1].content


# ---------- 十、失败与缺参也要占预算 ----------

class _TwoRequiredArgsTool:
    """两个必填参数的工具，用来触发「缺参」分支"""

    name = "common_tool"

    def __init__(self):
        self.calls = 0

    async def _arun(self, query: str, level: str):
        self.calls += 1
        return "ok"


class _BoomTool:
    """直接抛异常的工具"""

    name = "common_tool"

    def __init__(self):
        self.calls = 0

    async def _arun(self, query: str):
        self.calls += 1
        raise RuntimeError("模拟工具内部故障")


async def test_missing_params_still_consume_budget(fake_tools):
    """
    参数没补齐也算调过一次。

    模型一直用错参数重试，正是这个上限要治的场景——不占预算就等于没限制。
    """
    tool = fake_tools(_TwoRequiredArgsTool())

    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={"query": "x"}, round=1,
               tool_call_counts={})
    )

    assert tool.calls == 0, "参数不齐不该真的执行"
    assert "缺少必填参数" in out["messages"][-1].content
    assert out["tool_call_counts"] == {"common_tool": 1}


async def test_tool_exception_still_consumes_budget(fake_tools):
    """工具抛异常同样占预算，异常原文不外泄。"""
    fake_tools(_BoomTool())

    out = await react_agent.tool_exec_node(
        _state(action="common_tool", action_args={"query": "x"}, round=1,
               tool_call_counts={})
    )

    content = out["messages"][-1].content
    assert "执行出错" in content
    assert "模拟工具内部故障" not in content, "异常原文不该进模型上下文"
    assert out["tool_call_counts"] == {"common_tool": 1}


# ---------- 十一、真实图上的典型业务路径 ----------

class _FakeSkillTool:
    """假的 load_skill_tool"""

    name = "load_skill_tool"

    def __init__(self):
        self.calls = 0

    async def _arun(self, name: str):
        self.calls += 1
        return "# 出题剧本：1. 调用 question_set_tool 生成题目"


class _ScriptedLLM:
    """按脚本依次给出动作，模拟一条确定的对话路径"""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        content = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return SimpleNamespace(
            content=content, tool_calls=[], invalid_tool_calls=[]
        )


async def test_real_graph_business_path_completes(fake_tools, monkeypatch):
    """
    真实图跑一条典型业务路径：加载剧本 → 出题 → 作答。

    确认 8 轮 / 每工具 3 次的预算不会把正常流程掐断，
    也不会让最后一轮拿不到答案。
    """
    skill = fake_tools(_FakeSkillTool())
    qst = fake_tools(_CountingQST())
    # get_llm 每轮都会被调用一次，所以脚本对象要复用同一个，
    # 否则每轮都从头开始，路径就不是设计的那条了。
    llm = _ScriptedLLM([
        '{"thought":"先加载剧本","action":"load_skill_tool",'
        '"action_args":{"name":"question_variant"},"final_result":""}',
        '{"thought":"出题","action":"question_set_tool",'
        '"action_args":{"query":"解方程"},"final_result":""}',
        '{"thought":"够了","action":"","action_args":{},"final_result":"题目如下"}',
    ])
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: llm)

    app = react_agent.get_app()
    result = await app.ainvoke(
        _state(user_input="给我出几道题", tool_call_counts={})
    )

    assert result["final_result"] == "题目如下"
    assert skill.calls == 1
    assert qst.calls == 1
    assert result["tool_call_counts"] == {
        "load_skill_tool": 1,
        "question_set_tool": 1,
    }
    assert result["round"] == 3


async def test_real_graph_never_leaks_internal_notice_to_user(fake_tools, monkeypatch):
    """
    真实图回归：工具一直失败、模型一直没自答时，用户也不能收到内部指令。

    走完整张图——单测只调 _final_answer_from_observations 验证不到
    「这个兜底真的被用上了」这一步。
    """
    fake_tools(_BoomTool())
    llm = _ScriptedLLM([
        '{"thought":"再试一次","action":"common_tool",'
        '"action_args":{"query":"x"},"final_result":""}',
    ])
    monkeypatch.setattr(react_agent, "get_llm", lambda *a, **kw: llm)

    app = react_agent.get_app()
    result = await app.ainvoke(
        _state(user_input="给我出几道题", tool_call_counts={})
    )

    final = result["final_result"]
    assert final, "不能是空串"
    assert "没有得到有效结果" in final, "该走面向用户的兜底文案"
    assert "common_tool" in final, "要说清是哪个工具出的问题"
    assert "【工具预算】" not in final, "内部提示不能发给用户"
    assert "【工具执行】" not in final
    assert "请不要再调用它" not in final


# ---------- 辅助 ----------

async def _async_msg(content):
    """一个可 await 的假响应"""
    return SimpleNamespace(content=content, tool_calls=[], invalid_tool_calls=[])
