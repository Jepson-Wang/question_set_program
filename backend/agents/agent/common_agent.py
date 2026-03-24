import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from dotenv import load_dotenv

from backend.core.single_tool import singleton_method

load_dotenv()

COMMON_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个回答通用问题的助手，负责回答解题步骤，解题思路，解题方法等
            根据用户的输入，选择性的回答以下问题：
            1. 回答这个题的解题步骤，解题思路，解题方法等
            2. 回答这个题的相关问题，如这个题的难度，涉及的知识点等
            3. 回答用户的其他问题
            """),
    ("user", "{input}")  # 使用"user"角色而不是"human"
])

@singleton_method
def build_common_agent(streaming: bool = False) -> CompiledStateGraph[GraphState] | None:
    """
    负责其他一般性回答
    """
    model = os.getenv('COMMON_MODEL')
    if not model:
        agent = get_llm(streaming=streaming)
    else:
        agent = get_llm(model=model, streaming=streaming)
    return agent

@singleton_method
def common_node(state: GraphState) -> GraphState:
    """
    负责其他一般性回答
    :param state:
    :return:
    """

    print('正在初始化common_agent')
    user_input = state['input']

    # 进行大模型调用相关操作
    common_agent = build_common_agent()
    common_chain = COMMON_PROMPT | common_agent
    response = common_agent.invoke({'input': user_input})
    # 将回答返回给state,并追加到result
    response_text = extract_text_from_response(response)
    state['result'] = state['result'] + '\n' + response_text
    return state

async def async_common_node(state: GraphState) -> GraphState:
    user_input = state['input']
    common_agent = build_common_agent(streaming=True)
    common_chain = COMMON_PROMPT | common_agent
    response = await common_chain.ainvoke({'input': user_input})
    state['result'] = extract_text_from_response(response)
    return state