from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from backend.model import Base


class MemoryRecord(Base):
    """
    一轮对话的原文，只增不改。

    短期窗口（Redis）挤出来的记录会落到这里。原文永不压缩：一条几百字节，存得起；
    摘要是从它重算出来的派生数据，摘坏了、换了提示词，重跑一遍就行。
    """
    __tablename__ = 'memory_record'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键')
    user_id = Column(Integer, nullable=False, comment='用户ID')
    session_id = Column(Integer, nullable=False, comment='会话ID')
    user_text = Column(Text, nullable=False, comment='用户说的话')
    model_text = Column(Text, nullable=False, comment='模型的回答')
    # 归档是「至少一次」语义：写库成功但 ack 失败时会重试。用原始条目的哈希做唯一键，
    # 重复写入会被数据库直接拦下，不需要先查再写（那样仍然有并发窗口）
    fingerprint = Column(String(40), nullable=False, unique=True, comment='原始条目的 sha1，用于去重')
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp(), comment='归档时间')

    __table_args__ = (
        Index('ix_memory_record_session', 'user_id', 'session_id', 'id'),
    )


class MemoryDigest(Base):
    """
    一个会话一条摘要，由 memory_record 的原文重算得到。

    covered_until_id 记录这份摘要覆盖到了哪条记录，用来判断「新攒了多少条还没进摘要」。
    """
    __tablename__ = 'memory_digest'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键')
    user_id = Column(Integer, nullable=False, comment='用户ID')
    session_id = Column(Integer, nullable=False, comment='会话ID')
    summary = Column(Text, nullable=False, comment='会话要点摘要')
    covered_until_id = Column(Integer, nullable=False, comment='摘要覆盖到的最大 memory_record.id')
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp(), comment='更新时间')

    __table_args__ = (
        UniqueConstraint('user_id', 'session_id', name='uq_memory_digest_session'),
    )
