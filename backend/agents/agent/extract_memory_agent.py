"""
记忆精炼 Agent：将口语化对话列表转化为客观第三方记录，写入向量存档。
Prompt 从 backend/agents/skills/memory_refinement/SKILL.md 读取，实现单一事实源。
"""
import json
import os

import dotenv
from langchain_core.messages import SystemMessage, HumanMessage

from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import load_skill
from backend.core.single_tool import singleton_method
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

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


def _flatten_memories(memories: list[dict]) -> list[dict[str, str]]:
    """
    把 MemoryUnit（{'memory': {...}, 'timestamp': ...}）压平成
    SKILL.md 示例里的 {'user_memory', 'model_memory'} 形式，避免多余层级干扰模型。
    """
    flat: list[dict[str, str]] = []
    for item in memories:
        unit = item.get('memory', {}) if isinstance(item, dict) else {}
        flat.append({
            'user_memory': unit.get('user_memory', ''),
            'model_memory': unit.get('model_memory', ''),
        })
    return flat


def _parse_refined_memories(content: str) -> list[dict]:
    """
    解析精炼结果。模型可能带 Markdown 围栏或前后缀说明，
    这里只截取最外层 JSON 数组，解析失败时返回空列表而不是抛异常
    （归档失败不应该拖垮整条记忆写入链路）。
    """
    if not isinstance(content, str):
        logger.error("记忆精炼返回了非文本内容：%s", type(content))
        return []

    start = content.find('[')
    end = content.rfind(']')
    if start == -1 or end == -1 or end < start:
        logger.error("记忆精炼输出中找不到 JSON 数组：%s", content[:200])
        return []

    try:
        parsed = json.loads(content[start:end + 1])
    except json.JSONDecodeError as e:
        logger.error("记忆精炼输出解析失败：%s | 原文：%s", e, content[:200])
        return []

    if not isinstance(parsed, list):
        return []

    result: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        # SKILL.md 里约定的键是 text，这里同时兼容历史的 memory 键
        text = item.get('text') or item.get('memory') or ''
        if not text:
            continue
        tags = item.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        elif not isinstance(tags, list):
            tags = []
        result.append({'text': text, 'tags': tags})
    return result


async def get_extract_memory(memory: list[dict]) -> list[dict]:
    """
    调用记忆精炼智能体，精炼记忆
    :param memory: 原始短期记忆列表（MemoryUnit）
    :return: 精炼后的记忆列表，每项形如 {'text': str, 'tags': list[str]}
    """
    if not memory:
        return []

    system_body = load_skill("memory_refinement")
    llm = build_extract_memory_agent()
    response = await llm.ainvoke([
        SystemMessage(content=system_body),
        HumanMessage(content=json.dumps(_flatten_memories(memory), ensure_ascii=False)),
    ])
    return _parse_refined_memories(response.content)
