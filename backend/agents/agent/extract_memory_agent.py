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
            6. 输出内容应严格按照下面的格式来

            示例输入
            [
            {user_memory: "哎呀，我其实特别讨厌吃香菜，然后明天早上大概 8 点吧，我得去趟公司开会，烦死了。"
            model_memory: "理解你的感受！讨厌香菜是很常见的口味偏好，明天开会记得提前准备，祝你顺利～"},
            
            {user_memory: "今天下雨没带伞，上班迟到了，好郁闷。"
            model_memory: "下雨没带伞确实麻烦，迟到别太自责，下次可以提前看天气预报哦～"}
            ]
            
            
            示例输出
            [{"用户表示讨厌吃香菜，计划次日早上8点去公司开会且情绪烦躁；模型理解用户感受，提及讨厌香菜是常见偏好，并提醒用户提前准备开会。",
             "tags": ["用户喜恶","用户需求"],
            },
            
            {"用户因下雨没带伞上班迟到，情绪郁闷；模型表示理解，安慰用户别自责并建议提前看天气预报。",
             "tags": ["用户喜恶","用户需求","建议"],
            }]
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

async def get_extract_memory(memory:list[dict[str, str]]) -> list[dict[str,str]]:
    """
    调用记忆精炼智能体，精炼记忆
    :param memory:
    :return:
    """

    memory_agent = build_extract_memory_agent()
    memory_chain = EXTRACT_MEMORY_PROMPT | memory_agent
    extracted_memory = await memory_chain.invoke({"input": memory})

    return extracted_memory
