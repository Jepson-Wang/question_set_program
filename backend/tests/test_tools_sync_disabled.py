import pytest
from sympy import Lambda

from backend.agents.tools import TOOLS, TOOL_MAP

# 例外：load_skill_tool 只是读一个很小的 SKILL.md（loader 还带 mtime 缓存），同步实现不会阻塞事件循环。
# 把例外写成清单，比在测试里 skip 更清楚：结果里不会多出一条跳过，新增工具也不会悄悄跟着被放过。
SYNC_ALLOWED = {"load_skill_tool"}


@pytest.mark.parametrize("tool",[t for t in TOOLS if t.name not in SYNC_ALLOWED],ids=lambda t:t.name)
def test_sync_run_is_disabled(tool):
    """除清单里的例外之外，所有工具只走异步路径，同步_run必须显式拒绝，不能悄悄阻塞事件循环"""
    with pytest.raises(NotImplementedError):
        tool._run()

def test_sync_allowed_tool_really_works():
    """例外也要测：load_skill_tool 的同步入口要能正常读出剧本，而不是抛异常"""
    result = TOOL_MAP["load_skill_tool"]._run(name="question_variant")
    assert "【Skill: question_variant】已加载" in result

def test_sync_agent_functions_are_gone():
    from backend.agents.agent import common_agent,extract_agent,question_set_agent

    assert not hasattr(common_agent,"common_tool")
    assert not hasattr(extract_agent,"extract_tool")
    assert not hasattr(question_set_agent,"question_set_tool")
