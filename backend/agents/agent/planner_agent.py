import json
import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from dotenv import load_dotenv
load_dotenv()

PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个任务规划助手，负责根据用户请求规划和调度以下智能体
            -extract : 提取请求中所给题目的难度，知识点,并最终生成题目
            -common : 其他情况，比如不是生成题目，而是其他问题，比如解释知识点，或者其他问题。
        约束规则:
        1. 你将在上面的智能体中选择一个，根据用户请求进行调度。
        2. 你只能选择一个智能体进行调用。
        3. 需要根据用户请求，判断是提取知识点还是生成题目。
        4. 默认调用为extract
        5. 若用户请求中包含解释知识点，或者其他问题，再调用common智能体。
        最终目的:生成一个符合要求的题目
        输出格式示例:
        {{
            "route": "extract",
        }}
        """),
    ("user", "{input}")
])

def build_planner_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责任务规划和调度的智能体
    :return:
    """

    model = os.getenv('PLANNER_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent

def planner_node(state: GraphState) -> GraphState:
    """
    负责任务规划和调度
    :param state:
    :return:
    """

    print('正在初始化planner_agent')
    user_input = state['input']
    planner_agent = build_planner_agent()
    planner_chain = PLANNER_PROMPT | planner_agent
    response = planner_chain.invoke({'input': user_input})

    # 提取文本内容
    response_text = extract_text_from_response(response)

    # 尝试解析 JSON 格式的响应
    try:
        # 尝试从文本中提取 JSON
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            json_str = response_text[json_start:json_end]
            parsed = json.loads(json_str)
            route = parsed.get('route', '').lower()
        else:
            route = response_text.strip().lower()
    except:
        route = response_text.strip().lower()

    if route not in ['extract', 'question_set', 'analyse', 'common']:
        route = 'common'

    state['route'] = route

    print(route)

    return state

async def async_planner_node(state: GraphState) -> GraphState:
    user_input = state['input']
    planner_agent = build_planner_agent()
    planner_chain = PLANNER_PROMPT | planner_agent
    response = await planner_chain.ainvoke({'input': user_input})
    response_text = extract_text_from_response(response)
    try:
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            parsed = json.loads(response_text[json_start:json_end])
            route = parsed.get('route', '').lower()
        else:
            route = response_text.strip().lower()
    except:
        route = response_text.strip().lower()
    if route not in ['extract', 'question_set', 'analyse', 'common']:
        route = 'common'
    state['route'] = route
    return state