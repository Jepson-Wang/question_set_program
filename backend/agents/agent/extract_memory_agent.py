"""
步骤
1. 先定义一个提示词模板
2. 调用get_llm方法获取到大模型实例
3. 完成大模型
"""
import os

import dotenv
from langchain_core.prompts import ChatPromptTemplate

from backend.agents.agent.tools import get_llm, GraphState
from langgraph.graph.state import CompiledStateGraph

from backend.core.single_tool import singleton_method

EXTRACT_MEMORY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """
            作用：你是一个记忆精炼专家。你需要将用户的口语化输入转化为一段客观、精炼的第三方记录文本。

            注意：
            1. 去除语气词、重复内容和无关噪音。
            2. 保留核心事实、观点和情感倾向。
            3. 将第一人称（我）转换为第三人称（用户），以便存档。
            4. 将重复的内容合并。
            5. 输出内容应适合作为向量检索的索引。

            示例
            输入：哎呀，我其实特别讨厌吃香菜，然后明天早上大概 8 点吧，我得去趟公司开会，烦死了。
            输出：用户表示讨厌香菜。用户计划于次日早上 8 点前往公司参加会议，情绪表现为烦躁。
            """),
    ("user", "{input}")  # 使用"user"角色而不是"human"
])

dotenv.load_dotenv('.env')

@singleton_method
def build_extract_memory_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责记忆精炼的智能体
    :return:
    """

    extract_model = os.getenv('EXTRACT_MODEL')

    if not extract_model:
        agent = get_llm()
    else:
        agent = get_llm(model=extract_model)

    return agent


