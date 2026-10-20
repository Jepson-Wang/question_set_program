from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from backend.model import Base

# MySQL 的 DATETIME 默认只到秒，同一秒里先后活跃的两个会话会排不出先后。
# 侧边栏靠 updated_at 排序，所以存到微秒；时间由应用层写入，SQLite 测试库也能保留微秒
PreciseDateTime = DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')


class ChatSession(Base):
    """
    一个对话会话，给前端侧边栏用。

    和记忆系统是两回事：记忆是给模型看的，会被窗口裁剪、被摘要压缩；
    这里是给人看的，要完整、要能翻回去。session_id 由这张表自增生成，
    前端不再自己编号，也就不会撞号或伪造别人的会话。
    """
    __tablename__ = 'chat_session'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键，即 session_id')
    user_id = Column(Integer, nullable=False, comment='所属用户')
    title = Column(String(100), nullable=False, comment='会话标题')
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp(), comment='创建时间')
    updated_at = Column(PreciseDateTime, nullable=False, comment='最后活跃时间，由应用层写入')

    __table_args__ = (
        # 侧边栏查询是 WHERE user_id = ? ORDER BY updated_at DESC，联合索引让排序直接走索引
        Index('ix_chat_session_user_updated', 'user_id', 'updated_at'),
    )


class ChatMessage(Base):
    """
    会话里的一条消息，一条一行，只增不改。

    按条存而不是按轮存：Agent 中途失败时，用户的提问照样落库、照样显示，
    只是缺一条助手回复。归属关系通过 session 校验，这里不冗余 user_id。
    """
    __tablename__ = 'chat_message'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键')
    session_id = Column(Integer, nullable=False, comment='所属会话')
    role = Column(String(16), nullable=False, comment='user 或 assistant')
    content = Column(Text, nullable=False, comment='消息正文')
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp(), comment='创建时间')

    __table_args__ = (
        Index('ix_chat_message_session', 'session_id', 'id'),
    )
