"""
对话记忆的数据访问层：原文表 memory_record、会话摘要表 memory_digest。

返回的都是普通 dict 和 dataclass，不是 ORM 对象：会话一关，ORM 对象就处于游离状态，
调用方再访问属性容易踩到 DetachedInstanceError。
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.model.memory import MemoryDigest, MemoryRecord


@dataclass
class DigestState:
    summary: str
    covered_until_id: int


class MemoryMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def add_record(self, user_id: int, session_id: int, user_text: str,
                         model_text: str, fingerprint: str) -> bool:
        """
        写入一条对话原文。返回是否真的写入：fingerprint 已存在说明这条之前归档过，
        返回 False（幂等，调用方照常 ack 即可）。
        """
        async with self.session_factory() as session:
            session.add(MemoryRecord(
                user_id=user_id, session_id=session_id,
                user_text=user_text, model_text=model_text, fingerprint=fingerprint,
            ))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def list_records(self, user_id: int, session_id: int, limit: int) -> list[dict]:
        """取这个会话最近 limit 条原文，按时间从旧到新返回（摘要要按时序读）"""
        async with self.session_factory() as session:
            stmt = (
                select(MemoryRecord)
                .where(MemoryRecord.user_id == user_id, MemoryRecord.session_id == session_id)
                .order_by(MemoryRecord.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [{"id": r.id, "user_text": r.user_text, "model_text": r.model_text} for r in reversed(rows)]

    async def count_since(self, user_id: int, session_id: int, after_id: int) -> tuple[int, int]:
        """返回 (id 大于 after_id 的记录条数, 这个会话最大的记录 id)。用来判断该不该重算摘要"""
        async with self.session_factory() as session:
            base = (MemoryRecord.user_id == user_id, MemoryRecord.session_id == session_id)
            pending = (await session.execute(
                select(func.count()).select_from(MemoryRecord).where(*base, MemoryRecord.id > after_id)
            )).scalar_one()
            latest = (await session.execute(
                select(func.max(MemoryRecord.id)).where(*base)
            )).scalar()
        return pending, latest or 0

    async def get_digest(self, user_id: int, session_id: int) -> Optional[DigestState]:
        async with self.session_factory() as session:
            stmt = select(MemoryDigest).where(
                MemoryDigest.user_id == user_id, MemoryDigest.session_id == session_id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return DigestState(summary=row.summary, covered_until_id=row.covered_until_id)

    async def save_digest(self, user_id: int, session_id: int, summary: str, covered_until_id: int) -> None:
        """有则更新、无则插入"""
        async with self.session_factory() as session:
            stmt = select(MemoryDigest).where(
                MemoryDigest.user_id == user_id, MemoryDigest.session_id == session_id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                session.add(MemoryDigest(
                    user_id=user_id, session_id=session_id,
                    summary=summary, covered_until_id=covered_until_id,
                ))
            else:
                row.summary = summary
                row.covered_until_id = covered_until_id
            await session.commit()


_mapper: Optional[MemoryMapper] = None


def get_memory_mapper() -> MemoryMapper:
    """进程内单例。与 get_short_term_memory 同一个用法。"""
    global _mapper
    if _mapper is None:
        from backend.model import AsyncSessionLocal
        _mapper = MemoryMapper(AsyncSessionLocal)
    return _mapper
