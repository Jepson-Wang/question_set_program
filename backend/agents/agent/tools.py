import os
from typing import TypedDict,Any

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage

load_dotenv()

class react_format(TypedDict):
    thought : list[str]
    action: list[str]
    action_args: list[str]
    observation: list[str]
    round : int

class input_format(TypedDict):
    input: str
    memory: dict[str,list[dict[str,Any]]]

"""
重构一下GraphState的格式和代码
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


class GraphState(TypedDict):
    """多智能体工作流的状态定义"""

    user_input: str

    user_id: int
    session_id: int

    thought: str
    action: str
    action_args: dict
    messages: list[ToolMessage]  # 用于存前几轮的observation
    round: int

    final_result: str



model = os.getenv('MODEL_NAME')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')















