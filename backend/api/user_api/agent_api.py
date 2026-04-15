from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import logging
from typing import Optional

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.memory_manager import MemoryManager
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.agents.memory.vector_store_manager import VectorStoreManager
from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model import AsyncSessionLocal

from backend.api.dependencies import get_current_user
from backend.model.user import User
from backend.agents.agent.tools import GraphState
from backend.agents.agent.react_agent import get_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TextRequest(BaseModel):
    # 前端/历史代码可能传 `id` 或 `user_id`，这里都接收以避免 422
    id: Optional[int] = None
    user_id: Optional[int] = None
    session_id: int
    text: str


agent_router = APIRouter(prefix="/agent", tags=["agent"])

# 对记忆模块的各个类进行实例化
user_profile_mapper = UserProfileMapper(AsyncSessionLocal)
short_term_memory = ShortTermMemory(max_memory_size=10)
long_term_memory = LongTermMemory(user_profile_mapper, short_term_memory)
VectorStoreManager = VectorStoreManager()
memory_manager = MemoryManager(long_term_memory, short_term_memory, VectorStoreManager)


def _format_memory_context(short_memories: list) -> str:
    """将最近3轮原始短期记忆格式化为对话上下文字符串，直接拼接不经过LLM"""
    if not short_memories:
        return ""
    recent = short_memories[:3]
    lines = ["【近期对话记录】"]
    for mem in reversed(recent):  # 从旧到新展示，保持时序
        user_mem = mem.get('memory', {}).get('user_memory', '')
        model_mem = mem.get('memory', {}).get('model_memory', '')
        if user_mem:
            lines.append(f"用户：{user_mem}")
        if model_mem:
            lines.append(f"助手：{model_mem}")
    return "\n".join(lines)


@agent_router.post('/analyse')
async def analyse(request: TextRequest, token: str = None, user: User = Depends(get_current_user)) -> dict:
    if user:
        user_id = request.id if request.id is not None else request.user_id
        if user_id is None:
            raise HTTPException(status_code=422, detail="Missing field: id or user_id")
        session_id = request.session_id
        text = request.text

        # 获取近3轮短期记忆，格式化后拼接到 user_input 头部
        memory_data = await memory_manager.get_memory_for_planner(user_id, session_id)
        short_memories = memory_data.get('short_memory', [])
        memory_context = _format_memory_context(short_memories)

        if memory_context:
            user_input = f"{memory_context}\n\n【当前问题】\n{text}"
        else:
            user_input = text

        state: GraphState = {
            'user_input': user_input,
            'user_id': user_id,
            'session_id': session_id,
            'thought': '',
            'action': '',
            'action_args': {},
            'messages': [],
            'round': 0,
            'final_result': ''
        }

        app = get_app()
        result = await app.ainvoke(state)

        # 写入记忆：保存原始用户文本（不含历史上下文），避免记忆污染
        memory_unit = MemoryUnit(text, result.get('final_result', ''))
        await memory_manager.add_memory(user_id, session_id, memory_unit)

        return {
            'code': 200,
            'msg': 'success',
            'data': result
        }
    else:
        return {
            'code': 401,
            'msg': '未授权',
            'data': None
        }


async def _stream_generator(text: str, user_id: int, session_id: int):
    """
    流式执行 ReAct 图，按 SSE 格式逐步推送：
    - thinking：LLM 决定调用哪个 skill
    - observation：skill 执行结果
    - result：最终回答
    """
    memory_data = await memory_manager.get_memory_for_planner(user_id, session_id)
    short_memories = memory_data.get('short_memory', [])
    memory_context = _format_memory_context(short_memories)

    if memory_context:
        user_input = f"{memory_context}\n\n【当前问题】\n{text}"
    else:
        user_input = text

    state: GraphState = {
        'user_input': user_input,
        'user_id': user_id,
        'session_id': session_id,
        'thought': '',
        'action': '',
        'action_args': {},
        'messages': [],
        'round': 0,
        'final_result': ''
    }

    app = get_app()
    final_result = ''

    async for chunk in app.astream(state, stream_mode="updates"):
        # react_think 节点更新：推送思考过程或最终结果
        if 'react_think' in chunk:
            update = chunk['react_think']
            action = update.get('action')
            thought = update.get('thought', '')
            fr = update.get('final_result', '')

            if action:
                payload = json.dumps(
                    {'type': 'thinking', 'action': action, 'thought': thought},
                    ensure_ascii=False
                )
                yield f"data: {payload}\n\n"

            if fr:
                final_result = fr
                payload = json.dumps({'type': 'result', 'content': fr}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

        # execute_skill 节点更新：推送 skill 执行结果
        elif 'execute_skill' in chunk:
            update = chunk['execute_skill']
            messages = update.get('messages', [])
            if messages:
                obs = messages[-1].content
                payload = json.dumps({'type': 'observation', 'content': obs}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

    # 流结束后写入记忆
    if final_result:
        memory_unit = MemoryUnit(text, final_result)
        await memory_manager.add_memory(user_id, session_id, memory_unit)

    yield "data: [DONE]\n\n"


@agent_router.post('/analyse/stream')
async def analyse_stream(
    request: TextRequest,
    user: User = Depends(get_current_user)
):
    user_id = request.id if request.id is not None else request.user_id
    if user_id is None:
        raise HTTPException(status_code=422, detail="Missing field: id or user_id")

    return StreamingResponse(
        _stream_generator(request.text, user_id, request.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
