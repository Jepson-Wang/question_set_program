from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import logging

from backend.api.dependencies import get_current_user
from backend.model.user import User
from backend.agents.agent.agents import GraphState
from backend.agents.agent.nodes import build_graph, build_stream_graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TextRequest(BaseModel):
    text: str


agent_router = APIRouter(prefix="/agent", tags=["agent"])


@agent_router.post('/analyse')
def analyse(request: TextRequest, token, user: User = Depends(get_current_user)) -> dict:
    if user:
        agent = build_graph()
        text = request.text
        state: GraphState = {
            'input': text,
            'route': '',
            'result': '',
            'extract': {}
        }
        result = agent.invoke(state)
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
