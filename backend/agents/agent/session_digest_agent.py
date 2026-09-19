"""
会话摘要：把一个会话已归档的原文压成一段要点，作为后续请求的上下文。

摘要永远从原文重算，不在上一版摘要的基础上继续摘要——那样每压一次信息就失真一点，
几轮之后早期内容已经面目全非，而且原文若已被覆盖就无从回溯。原文在 memory_record 里
只增不改，重算的代价只是一次 LLM 调用。
"""
import asyncio
import os
import time

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import load_skill
from backend.core.config import load_env
from backend.core.single_tool import singleton_method
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

load_env()

# 单条记录截断：摘要只需要知道问过什么、卡在哪，不需要完整的解题过程
RECORD_USER_MAX_CHARS = 200
RECORD_MODEL_MAX_CHARS = 300
# 一次摘要的输入上限：超过就从最早的记录开始丢，保住最近的
DIGEST_INPUT_MAX_CHARS = 6000

# 单次摘要的限时：基础 20 秒，每千字加 10 秒，最多 60 秒。
# LLM 客户端本身没有超时（httpx Timeout(None)），不设的话卡住的请求会一直挂着
DIGEST_TIMEOUT_BASE = 20.0
DIGEST_TIMEOUT_PER_1K = 10.0
DIGEST_TIMEOUT_MAX = 60.0


@singleton_method
def build_session_digest_agent():
    """摘要用的模型可以比主模型便宜：DIGEST_MODEL 没配就回落到 MODEL_NAME"""
    model = os.getenv('DIGEST_MODEL')
    return get_llm(model=model) if model else get_llm()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def format_records(records: list[dict]) -> str:
    """
    把原文排成带序号的对话，模型按时序阅读。
    每条先截断，整体仍然超长时从最早的记录开始丢——最近发生的事对后续对话更有用。
    """
    blocks = [
        f"{index}. 用户：{_truncate(r['user_text'], RECORD_USER_MAX_CHARS)}\n"
        f"   助手：{_truncate(r['model_text'], RECORD_MODEL_MAX_CHARS)}"
        for index, r in enumerate(records, 1)
    ]
    while len(blocks) > 1 and sum(len(b) + 1 for b in blocks) > DIGEST_INPUT_MAX_CHARS:
        blocks.pop(0)
    return "\n".join(blocks)


def digest_timeout(input_chars: int) -> float:
    """按输入长度给单次摘要限时"""
    return min(DIGEST_TIMEOUT_BASE + input_chars / 1000 * DIGEST_TIMEOUT_PER_1K, DIGEST_TIMEOUT_MAX)


async def build_session_digest(records: list[dict]) -> str:
    """把一个会话的原文压成要点。没有原文时返回空字符串。"""
    if not records:
        return ""

    system_body = load_skill("session_digest")
    llm = build_session_digest_agent()
    payload = format_records(records)
    timeout = digest_timeout(len(payload))

    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system_body), HumanMessage(content=payload)]),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("会话摘要超时：输入 %s 字，限时 %.0f 秒", len(payload), timeout)
        raise
    logger.info("会话摘要完成：输入 %s 字，耗时 %.1f 秒", len(payload), time.perf_counter() - started)

    content = response.content if isinstance(response.content, str) else ""
    return content.strip()
