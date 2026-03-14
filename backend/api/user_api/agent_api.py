from fastapi import APIRouter,Depends
from pydantic import BaseModel

from backend.api.dependencies import get_current_user
from backend.model.user import User
from backend.agents.agent.agents import GraphState
import logging

from backend.agents.agent.nodes import build_graph

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TextRequest(BaseModel):
    text: str


agent_router = APIRouter(prefix="/agent", tags=["agent"])

@agent_router.post('/analyse')
def analyse(request: TextRequest,token,user : User = Depends(get_current_user)) -> dict:
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
