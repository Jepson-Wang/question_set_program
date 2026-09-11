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