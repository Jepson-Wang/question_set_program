import json
import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from dotenv import load_dotenv
load_dotenv()

ANALYSE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """
    你是一名专业的数学审核员，负责审核生成的数学题目是否符合要求。
    审核规则：
    1. 检查题目是否与所给的难度和知识点相符；
    2. 检查题目是否符合人教版数学教材的要求；
    3. 检查题目是否有明确的答案；
    4. 检查题目是否有足够的随机性，不能完全重复；
    5. 检查题目是否符合中小学生的认知，避免产生理解困难；
    6. 检查题目是否符合输出格式要求；
    输出格式：严格按照审核规则，输出审核结果
    参考示例：
    输入：解方程 2x+9=5x-3 难度：困难  知识点：一元一次方程
    输出：符合要求
    """),
    ("user", "{input}")  # 使用"user"角色而不是"human"
])

def build_analyse_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责审核生成的题目
    :return:
    """

    model = os.getenv('ANALYSE_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent

def analyse_node(state: GraphState) -> GraphState:
    """
    负责审核生成的题目
    :param state:
    :return:
    """

    print('正在初始化analyse_agent')
    result = state['result']

    # 进行大模型调用相关操作
    analyse_agent = build_analyse_agent()
    response = analyse_agent.invoke({'input': result})
    # 将审核结果返回给state,并追加到result
    response_text = extract_text_from_response(response)
    state['result'] = result + '\n' + response_text
    return state
