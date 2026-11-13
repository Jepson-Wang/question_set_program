from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.memory_manager import MemoryManager
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.core.session_lock import session_locks
from backend.dao.memory_mapper import MemoryMapper
from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model import AsyncSessionLocal

from backend.api.dependencies import get_current_user
from backend.api.user_api.session_api import require_own_session
from backend.dao.chat_mapper import get_chat_mapper
from backend.model.user import User
from backend.agents.agent.tools import GraphState
from backend.agents.agent.react_agent import get_app, format_profile


class TextRequest(BaseModel):
    # 用户身份只认 token，不再从请求体读 user_id：否则登录 A 就能传 B 的 id 读写 B 的记忆
    session_id: int
    text: str


agent_router = APIRouter(prefix="/agent", tags=["agent"])

# 对记忆模块的各个类进行实例化
user_profile_mapper = UserProfileMapper(AsyncSessionLocal)
short_term_memory = ShortTermMemory(max_memory_size=10)
long_term_memory = LongTermMemory(user_profile_mapper, short_term_memory)
memory_mapper = MemoryMapper(AsyncSessionLocal)
memory_manager = MemoryManager(long_term_memory, short_term_memory, memory_mapper)


def _format_memory_context(short_memories: list, session_digest: str = "") -> str:
    """
    拼出注入 user_input 的上下文：先放本次会话的要点，再放最近 3 轮原文。
    要点是早期对话压缩来的，原文是刚刚发生的，两者都不经过 LLM，直接拼接。
    """
    lines: list[str] = []
    if session_digest:
        lines.append("【本次会话要点】")
        lines.append(session_digest)

    recent = short_memories[:3]
    if recent:
        if lines:
            lines.append("")
        lines.append("【近期对话记录】")
        for mem in reversed(recent):  # 从旧到新展示，保持时序
            user_mem = mem.get('memory', {}).get('user_memory', '')
            model_mem = mem.get('memory', {}).get('model_memory', '')
            if user_mem:
                lines.append(f"用户：{user_mem}")
            if model_mem:
                lines.append(f"助手：{model_mem}")
    return "\n".join(lines)


async def _build_state(text: str, user_id: int, session_id: int) -> GraphState:
    """
    构建送进图的初始 state。analyse 和流式接口都走这里：
    state 在两处各拼一份，新增字段漏改一处，那条路径上就悄悄失效。
    """
    # 获取近3轮短期记忆，格式化后拼接到 user_input 头部
    memory_data = await memory_manager.get_memory_for_planner(user_id, session_id)
    short_memories = memory_data.get('short_memory', [])
    memory_context = _format_memory_context(short_memories, memory_data.get('session_digest', ''))

    if memory_context:
        user_input = f"{memory_context}\n\n【当前问题】\n{text}"
    else:
        user_input = text

    # 画像每个请求只读一次：本次请求内 user_profile_save_tool 若写了画像，后续 ReAct 轮次
    # 仍看到旧画像，下个请求才更新（可接受，它是稳定背景信息）
    profile_text = format_profile(memory_data.get('long_memory'))

    return {
        'user_input': user_input,
        'user_id': user_id,
        'session_id': session_id,
        'profile_text': profile_text,
        'thought': '',
        'action': '',
        'action_args': {},
        'messages': [],
        'round': 0,
        'final_result': '',
        # 每个工具本次请求已被调用的次数，用来给单个工具设调用上限。
        # 每次请求都从零开始——上限是「一次请求内」的预算，不是跨请求的。
        'tool_call_counts': {},
    }


@agent_router.post('/analyse')
async def analyse(request: TextRequest, user: User = Depends(get_current_user)) -> dict:
    if user:
        user_id = user.id
        session_id = request.session_id
        text = request.text

        chat_mapper = get_chat_mapper()
        # 归属校验留在锁外：它只读会话表，不碰会话内容，
        # 不该让排队等锁的请求互相拖住，更不该让越权请求占着别人的会话锁。
        await require_own_session(session_id, user_id, chat_mapper)

        # 同一会话的请求必须排队跑：写提问、跑图、写回答、写记忆是一个整体。
        # 不排队的话两个请求会交叉着写库，user1 的提问会配上 user2 的回答，问答配对直接串味；
        # 两次请求读到的上下文快照也会互相踩。
        # 排队保证的是「配对不串味」，不是「落库顺序 = 提问顺序」：
        # 两条并发提问谁先拿到锁，取决于谁先走到这里，这个先后本来就无从定义。
        async with session_locks.guard(session_id):
            await chat_mapper.add_message(session_id, "user", text)

            state = await _build_state(text, user_id, session_id)

            app = get_app()
            result = await app.ainvoke(state)

            # 先写展示用的消息，再写记忆：消息丢了用户看得见，记忆缺一轮只影响后续上下文
            await chat_mapper.add_message(session_id, "assistant", result.get('final_result', ''))

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
    - thinking：LLM 决定调用哪个 tool
    - observation：tool 执行结果
    - result：最终回答
    """
    state = await _build_state(text, user_id, session_id)

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

        # execute_tool 节点更新：推送 tool 执行结果
        elif 'execute_tool' in chunk:
            update = chunk['execute_tool']
            messages = update.get('messages', [])
            if messages:
                last_msg = messages[-1]
                obs = last_msg.content
                if last_msg.tool_call_id.startswith("load_skill_tool_"):
                    payload = json.dumps(
                        {'type': 'skill_loaded', 'content': obs},
                        ensure_ascii=False
                    )
                else:
                    payload = json.dumps({'type': 'observation', 'content': obs}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

    # 流结束后先存助手消息、再写入记忆，顺序理由同 analyse
    if final_result:
        await get_chat_mapper().add_message(session_id, "assistant", final_result)
        memory_unit = MemoryUnit(text, final_result)
        await memory_manager.add_memory(user_id, session_id, memory_unit)

    yield "data: [DONE]\n\n"


class _SessionStreamingResponse(StreamingResponse):
    """
    流式响应：不管以什么方式结束，都把会话锁还回去。

    为什么释放点要放在 ASGI 调用的最外层，而不是只靠生成器的 finally：
    客户端断开时，流会停在生成器的 yield 上，而 `async for` 被异常打断**并不会关闭生成器**
    （async generator 要等 GC 的 asyncgen finalizer 才跑 finally）。那之前这个会话一直是被锁住的，
    后来的请求全在排队——用户选了「排队等待」不是 409，等于该会话被悄悄卡死。

    为什么不用 BackgroundTask 兜底：Starlette 把 `await self.background()` 排在 `stream_response`
    **之后**，流内抛异常（客户端断开 → ClientDisconnect）会直接跳过它，锁永远不还。
    而且同步的 background callable 会被丢进线程池执行——在工作线程里 `asyncio.Lock.release()`
    唤醒不了阻塞在事件循环里的等待者（debug 模式下还会抛
    "Non-thread-safe operation invoked on an event loop other than the current one"），
    顺带还会和事件循环线程抢 `users` 计数。这两样都比没有兜底更危险。

    `__call__` 的 try/finally 没有这个问题：只要 ASGI 调用有返回或抛异常，锁一定还回去，
    而且这段代码就跑在事件循环线程上。
    """

    def __init__(self, *args, release=None, **kwargs):
        super().__init__(*args, **kwargs)
        # 释放动作是幂等的：生成器的 finally 也会调一次，谁先谁后都不影响。
        self._release = release or (lambda: None)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


@agent_router.post('/analyse/stream')
async def analyse_stream(
    request: TextRequest,
    user: User = Depends(get_current_user)
):
    # 归属校验和用户消息落库都放在开流之前：响应头一旦发出去就没法再返回 404 了
    chat_mapper = get_chat_mapper()
    # 归属校验留在锁外：越权请求不能启动 Agent，更不能占着别人的会话锁；
    # 它也不碰会话内容，排队等锁的请求没必要被它拖住。
    await require_own_session(request.session_id, user.id, chat_mapper)

    # 这里的锁必须活过整个 SSE 流。async with 在函数 return 时就释放了，
    # 那时流一个字节都还没发出去，后一个请求会直接插进来——所以手动 acquire / release。
    handle = await session_locks.acquire(request.session_id)
    try:
        # 用户消息仍然在开流之前落库（保住「用户消息落库在开流前」的既有语义），
        # 但要等拿到锁之后才写：写消息和写回答必须在同一次持锁里完成，
        # 否则并发请求的提问和回答会交叉，问答配对直接串味。
        await chat_mapper.add_message(request.session_id, "user", request.text)
    except BaseException:
        # 写不进去就没必要开流了；锁必须还回去，否则这个会话会被永久卡死。
        session_locks.release(handle)
        raise

    async def _locked_stream():
        try:
            async for chunk in _stream_generator(request.text, user.id, request.session_id):
                yield chunk
        finally:
            # 生成器正常结束、被关闭都会走到这里。
            # 注意：客户端断开时这一行未必执行得了——流会停在 yield 上，
            # 而 async for 被异常打断并不会关闭生成器。真正的兜底在下面的响应类里。
            session_locks.release(handle)

    return _SessionStreamingResponse(
        _locked_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
        release=lambda: session_locks.release(handle),
    )
