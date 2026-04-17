"""
记忆精炼 Agent：将口语化对话列表转化为客观第三方记录，写入向量存档。
Prompt 从 backend/agents/skills/memory_refinement/SKILL.md 读取，实现单一事实源。
"""
import os

import dotenv
from langchain_core.messages import SystemMessage, HumanMessage

from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import load_skill
from backend.core.single_tool import singleton_method

dotenv.load_dotenv('.env')


@singleton_method
def build_extract_memory_agent():
    """
    负责记忆精炼的智能体
    """
    extract_model = os.getenv('EXTRACT_MODEL')
    if not extract_model:
        agent = get_llm()
    else:
        agent = get_llm(model=extract_model)
    return agent


async def get_extract_memory(memory: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    调用记忆精炼智能体，精炼记忆
    :param memory: 原始短期记忆列表
    :return: 精炼后的记忆列表
    """
    system_body = load_skill("memory_refinement")
    llm = build_extract_memory_agent()
    response = await llm.ainvoke([
        SystemMessage(content=system_body),
        HumanMessage(content=str(memory)),
    ])
    return response
