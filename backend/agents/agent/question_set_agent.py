import json
import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from dotenv import load_dotenv
load_dotenv()

QUESTION_SET_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """
    你是一名拥有5年教学经验的数学一线教师，熟悉人教版数学教材的所有知识点和题型。
    核心任务：基于学生提供的参考题目，生成变式题（类似但不相同的题目）。
    约束规则：
    1. 题型必须严格匹配人教版数学教材，禁止超纲；
    2. 生成的题目要贴合中小学生的认知，避免产生理解困难；
    3. 输出总长度控制在200字以内。
    4. 输出的题目有多种题型，如选择题、填空题、解方程等，根据题型不同，输出不同的格式。
    5. 生成的题目要与原题相似但不相同，保持相同的解题思路和知识点，但改变数值、条件或表达方式。
    6. 生成的题目要有足够的随机性，不能完全重复原题。
    7. 确保生成的题目有明确的答案。
    8. 无论输入如何，都必须生成具体的数学题目，不能要求用户提供更多信息。
    输出格式：严格按照题型，题干，答案的格式输出
    参考示例：
    输入：解方程 2x+9=5x-3 难度：困难  知识点：一元一次方程
    输出：
    解方程
    解方程2 [3-4 (x-1/2)] + 9 = 5 [2x-(x+1)] - 3
    答案：x=27/13；

    """),
    ("user", "{input}")  # 使用"user"角色而不是"human"
])

def build_question_set_agent(streaming: bool = False) -> CompiledStateGraph[GraphState] | None:
    """
    负责根据提取到的知识点和难度，生成题目
    """
    model = os.getenv('QUESTION_SET_MODEL')
    if not model:
        agent = get_llm(streaming=streaming)
    else:
        agent = get_llm(model=model, streaming=streaming)
    return agent

def question_set_node(state: GraphState) -> GraphState:
    """
    负责根据提用户输入的题目和根据题目的知识点和难度，生成一个新题
    :param state:
    :return:
    """

    print('正在初始化question_set_agent')
    user_input = state['input']
    extract_input = state['extract']

    # 进行大模型调用相关操作
    question_set_agent = build_question_set_agent()
    question_set_chain = QUESTION_SET_PROMPT | question_set_agent
    # 构造更明确的输入提示
    enhanced_input = f"请基于以下参考题目生成一道变式题：\n\n{user_input}\n\n知识点要求：{extract_input}"
    response = question_set_chain.invoke({'input': enhanced_input})
    # 将生成的题目返回给state
    state['result'] = extract_text_from_response(response)
    return state

async def async_question_set_node(state: GraphState) -> GraphState:
    user_input = state['input']
    extract_input = state['extract']
    question_set_agent = build_question_set_agent(streaming=True)
    question_set_chain = QUESTION_SET_PROMPT | question_set_agent
    enhanced_input = f"请基于以下参考题目生成一道变式题：\n\n{user_input}\n\n知识点要求：{extract_input}"
    response = await question_set_chain.ainvoke({'input': enhanced_input})
    state['result'] = extract_text_from_response(response)
    return state