import json
import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from backend.core.single_tool import singleton_method

from dotenv import load_dotenv

load_dotenv()

EXTRACT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个专业的知识点和难度提取助手，
    你的任务是从用户提供的任何数学题目中提取出难度级别和涉及的知识点。
    1. 难度：简单、中等、困难
    2. 知识点：详细列出题目中涉及的数学知识点
    3. 无论用户如何表述，只要包含数学题目，你都应该直接提取信息，不要要求用户提供更多信息
    4. 必须返回一个json格式的字符串，格式如下:
    {{
        "difficulty": "难度级别",
        "knowledge_points": ["知识点1", "知识点2", ...]
    }}
    """),
    ("user", "{input}")  # 使用"user"角色而不是"human"
])

@singleton_method
def build_extract_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责任务分析和解释的智能体
    :return:
    """

    model = os.getenv('EXTRACT_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent

@singleton_method
def extract_node(state: GraphState) -> GraphState:
    """
    负责提取请求中知识点和难度
    :param state:
    :return:
    """

    print('正在初始化extract_agent')
    system_input = state['input']
    # 调用用户请求进行提取操作
    extract_agent = build_extract_agent()
    extract_chain = EXTRACT_PROMPT | extract_agent
    response = extract_chain.invoke({'input': system_input})
    # 将提取到的知识点和难度返回给state
    response_text = extract_text_from_response(response)

    # 尝试解析 JSON 格式的响应
    try:
        # 尝试从文本中提取 JSON
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            json_str = response_text[json_start:json_end]
            parsed = json.loads(json_str)
            state['extract'] = parsed
        elif isinstance(response, dict):
            state['extract'] = response
        else:
            state['extract'] = {}
    except:
        state['extract'] = {}
    return state

async def async_extract_node(state: GraphState) -> GraphState:
    system_input = state['input']
    extract_agent = build_extract_agent()
    extract_chain = EXTRACT_PROMPT | extract_agent
    response = await extract_chain.ainvoke({'input': system_input})
    response_text = extract_text_from_response(response)
    try:
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            parsed = json.loads(response_text[json_start:json_end])
            state['extract'] = parsed
        else:
            state['extract'] = {}
    except:
        state['extract'] = {}
    return state