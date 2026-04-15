"""
所有的Agent请求最先发到这里，通过ReAct进行统一调度
具体功能：
1. 调用skills，查询记忆模块，并写入记忆
2. 调用LangGraph中的Node，完成任务执行
3. 设定一个最大迭代轮次，超过之后，就只接关停
"""
from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import SKILLS, SKILL_MAP, get_skill_prompt

from langgraph.graph import StateGraph, END

from langchain_core.messages import ToolMessage
import json

from backend.agents.agent.tools import GraphState

from langchain_core.prompts import ChatPromptTemplate


"""
大模型返回结果示例
thought: 用户希望了解我的身份和功能，属于通用性介绍类问题，应调用 common_skill 进行解答。
action: common_skill
action_args: {
    "user_query": "你好，介绍一下自己"
}
Observation: 等待工具返回结果
"""

"""1. ReAct Node接收state中的input
2. 通过大模型返回结果
3. 将结果进行处理，需要将结果存入state中，供大模型下一轮使用，但貌似ReAct调用的结果不需要放入，因为无关紧要
4. 传给执行node，执行相关逻辑,并将结果存入state"""

"""
state格式
{
    'user_input' : str,

    user_id : int,
    session_id : int,

    'thought' : str
    'action' : str,
    'action_args' : ,
    'messages' : list[ToolMessage]，  #用于存前几轮的observation
    'round' : int,

    final_result : str
}
"""

"""
思考：
1.  应该将state中的memory字段删除，转而append到observation中
    原因： memory后续我要封装成一个skill，交给大模型自主决策

2.  我的各个node应该更新state中的哪个字段：
    react node，更新thought,action,action_args，round,final_result字段
    skill node 更新messages字段
"""


_REACT_SYSTEM_PROMPT = """# 角色
你是 ReAct 决策 Agent：基于 `user_input` 和 `messages` 上下文，循环「思考→行动」，决定调用工具或直接回答。

# 可用工具 (Skills)
只能从下列工具中选择，禁止编造不存在的工具：

""" + get_skill_prompt() + """

# 规则
1. 业务问题必须通过工具获取数据，不得凭空编造。
2. 每轮只输出一次「思考+行动」，不要模拟工具返回；真实 Observation 由系统写入 `messages`。
3. 每次只调用一个 skill。
4. 决策必须结合 `messages` 中前序工具的 `content`，而非只看 `user_input`。
5. 若工具和自身能力均无法解答，`final_result` 填："我不能解答用户的问题"。

# 输出格式
只返回一段合法 JSON，禁止 Markdown 代码块或额外解释。

- 需调工具：`action`=工具名，`action_args`=参数，`final_result`=`""`
- 信息已充足：`action`=`null`，`action_args`=`{{}}`，`final_result`=通俗易懂的最终回答

结构：
{{
    "thought": "分析上下文是否充足，确定下一步",
    "action": "工具名 或 null",
    "action_args": {{"参数名": "参数值"}},
    "final_result": "最终回答"
}}
"""

REACT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _REACT_SYSTEM_PROMPT),
    ("user", "{input}")
])


def react_think_node(state: GraphState) -> dict:
    """LLM思考：是否调用Skill、调用哪个"""
    llm_input = {
        "user_input": state['user_input'],
        "messages": state['messages'],
        "user_id": state['user_id'],
        "session_id": state['session_id']
    }
    llm = get_llm().bind_tools(SKILLS)
    ReAct_chain = REACT_PROMPT | llm
    response_content = ReAct_chain.invoke({"input": llm_input}).content
    response = json.loads(response_content)

    round = state['round'] + 1
    print('轮次:',round)
    print('输入:',llm_input)
    print('思考结果:',response["thought"])
    print('调用skill:',response["action"])
    print('调用skill参数:',response["action_args"])
    print('messages:',state['messages'])
    print('最终回答final_result:',response["final_result"])
    print('\n\n')

    print()

    return {
        'thought' : response["thought"],
        'action' : response["action"],
        'action_args' : response["action_args"],
        'round' : round,
        'final_result' : response["final_result"]
    }

# ----------------------
# 6. Skill 执行节点（手脚）
# ----------------------
async def skill_exec_node(state: GraphState) -> dict:
    """执行LLM选择的Skill"""
    func_name = state['action']
    args = state['action_args']

    # query_memory_skill 需要 user_id/session_id，LLM 不知道具体值，从 state 注入
    if func_name == "query_memory_skill":
        args['user_id'] = state['user_id']
        args['session_id'] = state['session_id']

    # user_profile_*_skill 只需 user_id，由 state 注入
    if func_name in (
        "user_profile_save_skill",
        "user_profile_query_skill",
        "user_profile_delete_skill",
    ):
        args['user_id'] = state['user_id']

    # 图节点本身在事件循环中运行，直接 await 异步实现，避免 _run 的 sync/async 桥接
    skill = SKILL_MAP.get(func_name)
    if skill:
        res = await skill._arun(**args)
    else:
        res = f"未知技能：{func_name}"

    messages = state['messages']
    messages.append(
        ToolMessage(content=res,
                    tool_call_id=f"{func_name}_{state['round']}"))

    return {
        'messages': messages
    }

# ----------------------
# 7. 路由决策：是否继续ReAct循环
# ----------------------
def should_continue(state: GraphState) -> str:
    """
    自主决策：
    - 还有工具要调用且未超上限 → 回到执行节点
    - 没有 → 结束，回答用户
    """
    print('正在执行should_continue_node')
    if state['action'] != 'null' and state['round'] < 5:
        return "execute_skill"
    return END

# ----------------------
# 8. 构建 LangGraph 图
# ----------------------
workflow = StateGraph(GraphState) #type:ignore

# 添加节点

workflow.add_node("react_think", react_think_node) #type:ignore
workflow.add_node("execute_skill", skill_exec_node) #type:ignore

# 入口
workflow.set_entry_point("react_think")

# 条件边：自主决定下一步！
workflow.add_conditional_edges(
    "react_think",
    should_continue
)

# 执行完工具 → 回到思考
workflow.add_edge("execute_skill", "react_think")

# 编译
def get_app():
    app = workflow.compile()
    return app
