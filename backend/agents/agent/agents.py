import os
from typing import TypedDict, Literal

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

load_dotenv()

def extract_text_from_response(response) -> str:
    """
    从 agent 响应中提取文本内容
    """
    if isinstance(response, str):
        return response
    elif isinstance(response, dict):
        # 如果是字典，尝试从 output 字段获取
        if 'output' in response:
            output = response['output']
            if isinstance(output, str):
                return output
            elif isinstance(output, BaseMessage):
                return output.content if hasattr(output, 'content') else str(output)
        # 尝试直接获取 messages 中的最后一个消息
        if 'messages' in response and response['messages']:
            last_message = response['messages'][-1]
            if isinstance(last_message, BaseMessage):
                return last_message.content if hasattr(last_message, 'content') else str(last_message)
        # 如果都不行，转换为字符串
        return str(response)
    elif isinstance(response, BaseMessage):
        return response.content if hasattr(response, 'content') else str(response)
    else:
        return str(response)

class GraphState(TypedDict):
    """多智能体工作流的状态定义"""

    input: str
    route: Literal['extract', 'question_set', 'analyse', 'common']
    extract: dict[str, str]
    memory : dict[str,str]
    result: str


model = os.getenv('MODEL_NAME')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')

#先创建几个大模型
def get_llm(model:str=model)  :
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3
    )
    return llm


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

def build_question_set_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责根据提取到的知识点和难度，生成题目
    :return:
    """

    model = os.getenv('QUESTION_SET_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent

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

def build_common_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责其他一般性回答
    :return:
    """

    model = os.getenv('COMMON_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent

def build_image_geng_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责根据用户输入，生成图片
    :return:
    """

    model = os.getenv('IMAGE_GENE_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent
