"""
会话与消息的数据访问层：chat_session、chat_message。

和 memory_mapper 一样只返回普通 dict，不把 ORM 对象带出会话。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from backend.model.chat import ChatMessage, ChatSession

DEFAULT_TITLE = "新对话"
TITLE_MAX_LEN = 30


def _session_dict(s: ChatSession) -> dict:
    return {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}


def _title_from(text: str) -> str:
    """首条提问压成一行、截前 30 个字当标题；全是空白就保留默认标题"""
    return " ".join(text.split())[:TITLE_MAX_LEN] or DEFAULT_TITLE


class ChatMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def create_session(self, user_id: int) -> dict:
        now = datetime.now()
        async with self.session_factory() as session:
            row = ChatSession(user_id=user_id, title=DEFAULT_TITLE, created_at=now, updated_at=now)
            session.add(row)
            await session.commit()
            return _session_dict(row)

    async def list_sessions(self, user_id: int, limit: int) -> list[dict]:
        """最近活跃的在前。updated_at 相同时按 id 兜底，保证顺序稳定"""
        async with self.session_factory() as session:
            stmt = (
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [_session_dict(r) for r in rows]

    async def get_session(self, user_id: int, session_id: int) -> Optional[dict]:
        """按「会话 id + 所属用户」一起查：别人的会话和不存在的会话一样返回 None"""
        async with self.session_factory() as session:
            stmt = select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _session_dict(row) if row else None

    async def list_messages(self, session_id: int) -> list[dict]:
        """从旧到新。调用方负责先用 get_session 校验归属"""
        async with self.session_factory() as session:
            stmt = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
            rows = (await session.execute(stmt)).scalars().all()
        return [{"id": r.id, "role": r.role, "content": r.content, "created_at": r.created_at} for r in rows]

    async def add_message(self, session_id: int, role: str, content: str) -> None:
        """
        写一条消息，同一个事务里刷新会话的活跃时间；
        如果这是会话的第一条提问（标题还是默认值），顺手用它生成标题。
        """
        now = datetime.now()
        async with self.session_factory() as session:
            session.add(ChatMessage(session_id=session_id, role=role, content=content, created_at=now))
            row = await session.get(ChatSession, session_id)
            if row is not None:
                row.updated_at = now
                if role == "user" and row.title == DEFAULT_TITLE:
                    row.title = _title_from(content)
            await session.commit()


_mapper: Optional[ChatMapper] = None


def get_chat_mapper() -> ChatMapper:
    """进程内单例，用法同 get_memory_mapper"""
    global _mapper
    if _mapper is None:
        from backend.model import AsyncSessionLocal
        _mapper = ChatMapper(AsyncSessionLocal)
    return _mapper
