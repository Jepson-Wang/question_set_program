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
from backend.agents.agent.graph_build import build_graph, build_stream_graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TextRequest(BaseModel):
    # 前端/历史代码可能传 `id` 或 `user_id`，这里都接收以避免 422
    id: Optional[int] = None
    user_id: Optional[int] = None
    session_id: int
    text: str


agent_router = APIRouter(prefix="/agent", tags=["agent"])

#对记忆模块的各个类进行实例化
user_profile_mapper = UserProfileMapper(AsyncSessionLocal)
short_term_memory = ShortTermMemory(max_memory_size = 10)
long_term_memory = LongTermMemory(user_profile_mapper,short_term_memory)
VectorStoreManager = VectorStoreManager()
memory_manager = MemoryManager(long_term_memory,short_term_memory,VectorStoreManager)


@agent_router.post('/analyse')
async def analyse(request: TextRequest,token: str = None, user: User = Depends(get_current_user)) -> dict:
    if user:
        user_id = request.id if request.id is not None else request.user_id
        if user_id is None:
            raise HTTPException(status_code=422, detail="Missing field: id or user_id")
        session_id = request.session_id
        agent = build_graph(user_id, session_id)
        text = request.text

        #memory这个str可以与input合并，格式{
        #   "input": "...",
        #   "memory": "..."
        # }
        memory_str = await memory_manager.get_memory_for_planner(user_id, session_id)
        memory_json = {
            "input": text,
            "memory": memory_str
        }

        

        state: GraphState = {
            'input': memory_json,
            'user_id': user_id,
            'session_id': session_id,
            'route': '',
            'result': '',
            'extract': {}
        }
        result = agent.invoke(state)

        #将result中的memory转换为json字符串
        user_memory = result['input']
        model_memory = result['result']
        memory_unit = MemoryUnit(user_memory, model_memory)
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


async def _stream_generator(text: str):
    """
    异步生成器：驱动 LangGraph 流式执行，按 SSE 格式逐块 yield
    stream_mode="messages" 会在每个节点产出 token 时触发，
    chunk[0] 是 BaseMessage，chunk[1] 是元数据（含 langgraph_node 等）
    """
    agent = build_stream_graph()
    state: GraphState = {
        'input': text,
        'route': '',
        'result': '',
        'extract': {}
    }

    # 只对最终输出节点的 token 做流式推送，中间节点（planner/extract）静默处理
    output_nodes = {'question_set', 'common'}

    async for chunk in agent.astream(state, stream_mode="messages"):
        message, metadata = chunk
        node_name = metadata.get('langgraph_node', '')
        if node_name not in output_nodes:
            continue
        content = getattr(message, 'content', '')
        if not content:
            continue
        payload = json.dumps({'node': node_name, 'content': content}, ensure_ascii=False)
        yield f"data: {payload}\n\n"

    yield "data: [DONE]\n\n"


@agent_router.post('/analyse/stream')
async def analyse_stream(
    request: TextRequest,
    user: User = Depends(get_current_user)
):
    return StreamingResponse(
        _stream_generator(request.text),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
