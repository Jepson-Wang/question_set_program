import json
import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from dotenv import load_dotenv

from backend.core.single_tool import singleton_method

load_dotenv()

# PLANNER_PROMPT = ChatPromptTemplate.from_messages([
#     ("system", """你是一个任务规划助手，负责根据用户请求规划和调度以下智能体
#             -extract : 提取请求中所给题目的难度，知识点,并最终生成题目
#             -common : 比如不是生成题目，而是解释知识点，解答这道题等。
#         约束规则:
#         1. 你将在上面的智能体中选择一个，根据用户请求进行调度，默认调用为extract。
#         2. 你只能选择一个智能体进行调用。
#         3. 需要根据用户请求，判断是提取知识点还是生成题目。
#         4. 如果用户输入中含有“提取知识点”、“生成题目”、“提取分析”等关键词，调用extract智能体。
#         5. 若用户请求中包含解释知识点，或者解答这个题目，再调用common智能体。
#         最终目的:生成一个符合要求的题目
#         输出格式示例:
#         {{
#             "route": "extract",
#         }}
#         """),
#     ("user", "{input}")
# ])

PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个任务规划助手，严格根据用户请求调度以下两个智能体：
        - extract：用于用户要求**生成题目、生成变式题、提取知识点、提取题目难度、提取题目分析**等场景
        - common：用于用户要求**解释知识点、解答题目、给出答案、题目解析、讲解思路**等场景
        
        约束规则：
        1. 必须且只能选择一个智能体返回
        2. 只要用户输入包含【生成题目、生成变式题】，一律路由 extract
        3. 只要用户输入是【解释、解答、答案、解析、讲解、说明，、提取知识点、提取难度、提取分析】知识点/题目，一律路由 common
        4. 默认路由：extract
        5. 禁止添加任何解释、文字、标点，只输出指定格式
        
        输出格式（严格JSON）：
        {{
            "route": "extract" 或 "common"
        }}
        """),
    ("user", "{input}")
])

@singleton_method
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