from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.api.dependencies import get_current_user
from backend.dao.chat_mapper import ChatMapper, get_chat_mapper
from backend.model.user import User

session_router = APIRouter(prefix='/sessions', tags=['session'])

SESSION_LIST_LIMIT = 50


class SessionResponse(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


async def require_own_session(session_id: int, user_id: int, mapper: ChatMapper) -> dict:
    """
    会话不存在、或者不属于当前用户，一律 404。
    不用 403：403 等于告诉对方「这个 id 存在，只是不是你的」，可以被用来枚举会话。
    """
    session = await mapper.get_session(user_id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return session


@session_router.post('', response_model=SessionResponse)
async def create_session(user: User = Depends(get_current_user)):
    return await get_chat_mapper().create_session(user.id)


@session_router.get('', response_model=list[SessionResponse])
async def list_sessions(user: User = Depends(get_current_user)):
    return await get_chat_mapper().list_sessions(user.id, limit=SESSION_LIST_LIMIT)


@session_router.get('/{session_id}/messages', response_model=list[MessageResponse])
async def list_messages(session_id: int, user: User = Depends(get_current_user)):
    mapper = get_chat_mapper()
    await require_own_session(session_id, user.id, mapper)
    return await mapper.list_messages(session_id)
