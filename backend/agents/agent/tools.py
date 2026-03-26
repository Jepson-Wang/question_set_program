import os
from typing import TypedDict, Literal, Any

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from backend.core.single_tool import singleton_method

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

    input: dict[str,Any] = {
        "input": str,
        "memory": dict[str,list[dict[str,Any]]]
    }
    user_id : int
    session_id : int
    route: Literal['extract', 'question_set', 'analyse', 'common']
    extract: dict[str, str]
    result: str


model = os.getenv('MODEL_NAME')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')

@singleton_method
def get_llm(model:str=model, streaming:bool=False):
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        streaming=streaming
    )
    return llm













