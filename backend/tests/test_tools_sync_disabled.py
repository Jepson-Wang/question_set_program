import pytest
from sympy import Lambda

from backend.agents.tools import TOOLS


@pytest.mark.parametrize("tool",TOOLS,ids=lambda t:t.name)
def test_sync_run_is_disabled(tool):
    """所有工具只走异步路径，同步_run必须显式拒绝，不能悄悄阻塞时间循环"""
    if tool.name == "load_skill_tool":
        pytest.skip("load_skill_tool 是纯文件读取，同步实现无阻塞风险")
    with pytest.raises(NotImplementedError):
        tool._run()

def test_sync_agent_functions_are_gone():
    from backend.agents.agent import common_agent,extract_agent,question_set_agent

    assert not hasattr(common_agent,"common_tool")
    assert not hasattr(extract_agent,"extract_tool")
    assert not hasattr(question_set_agent,"question_set_tool")
