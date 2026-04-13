"""
所有的Agent请求最先发到这里，通过ReAct进行统一调度
具体功能：
1. 调用skills，查询记忆模块，并写入记忆
2. 调用LangGraph中的Node，完成任务执行
3. 设定一个最大迭代轮次，超过之后，就只接关停
"""
from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import SKILLS, SKILL_MAP

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


REACT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """# 角色设定
你是一个遵循 ReAct (Reasoning and Acting) 范式的智能决策 Agent。你的任务是深度分析用户输入与当前上下文，通过严谨的「思考→行动」循环，精准决定是调用外部工具（Skills）还是直接输出最终回答。

# 可用工具集 (Skills)
你只能从以下列表中选择工具，严禁编造不存在的工具。如果任务超出工具能力且你无法解答，必须明确告知用户。
1. **`question_set_skill`**: 专门用于生成题目、生成变式题。
2. **`extract_skill`**: 专门用于从文本或题目中提取核心知识点。
3. **`query_memory_skill`**: 专门用于查询用户的历史记忆或个性化数据。
4. **`common_skill`**: 用于解析题目、分析题目、讲解答案、解释知识点、答疑解惑或处理其他通用类型问题。

# 执行规则与边界 (强制遵守)
1. **按需调用，拒绝虚构**：所有业务级解答必须通过工具获取数据。禁止自行拼接编造业务逻辑。
2. **单步执行，等待观察**：每一轮决策你只需给出当前的「思考」和「行动」。不要尝试在一次输出中模拟工具的返回结果，你必须输出调用指令并等待系统传入真实的 Observation（在 `messages` 中）。
3. **全局上下文感知**：你的决策不应只看 `user_input`，必须综合分析 `messages` 中包含的前序工具返回结果（`content`）。
4. **单一动作原则**：每次调用只能触发一个具体的 skill。
5. **兜底策略**：如果现有工具和你的基础能力均无法完成任务，直接输出最终回答："我不能解答用户的问题"。

# 状态与输出格式要求
你必须且只能返回一段合法的 JSON 字符串，禁止包含任何 Markdown 代码块标记（如 ```json ）或其他多余解释。系统将根据你的 JSON 结构判断当前所处阶段：

**场景 A：需要调用工具获取信息（正在 ReAct 循环中）**
- `action` 填入目标工具名称。
- `action_args` 填入工具所需的参数。
- `final_result` 必须为空字符串 `""`。

**场景 B：信息已充足，无需调用工具（结束循环）**
- `action` 必须为 `null`。
- `action_args` 必须为空字典 `{{}}`。
- `final_result` 填入整合后的最终回答（要求通俗易懂，直接响应用户原始需求）。

### 标准 JSON 输出结构：
{{
    "thought": "你的思考过程：分析当前上下文信息是否充足？用户真实需求是什么？下一步需要调用工具还是直接回答？",
    "action": "工具名称 或 null",
    "action_args": {{
        "参数名": "参数值"
    }},
    "final_result": "最终给用户的自然语言回答"
}}

### 输入数据参考
- `user_input`: 用户的原始需求。
- `messages`: 包含前几轮交互的上下文。其中 `content` 是工具执行的真实返回结果，`tool_call_id` 是调用记录。
        
        """),
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
def skill_exec_node(state: GraphState) -> dict:
    """执行LLM选择的Skill"""
    func_name = state['action']
    args = state['action_args']

    # query_memory_skill 需要 user_id/session_id，LLM 不知道具体值，从 state 注入
    if func_name == "query_memory_skill":
        args['user_id'] = state['user_id']
        args['session_id'] = state['session_id']

    # 所有 skill 统一通过 SKILL_MAP 查找，调用 _run()
    skill = SKILL_MAP.get(func_name)
    if skill:
        res = skill._run(**args)
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
    - 还有工具要调用 → 回到执行节点
    - 没有 → 结束，回答用户
    """
    print('正在执行should_continue_node')
    tool_call = state['action']
    tool_args = state['action_args']
    round = state['round']
    round += 1
    state['round'] = round
    if tool_call and tool_args and round<=5:
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
