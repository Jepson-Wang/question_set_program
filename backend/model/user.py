from sqlalchemy import Integer, Column, String

from backend.model import Base


class User(Base):
    __tablename__ = 'user'

    id = Column(Integer,primary_key=True,autoincrement=True)
    username = Column(String(20),unique=True,nullable=False)
    password = Column(String(100),nullable=False)
    user_privilege = Column(Integer,default=0)  # 0: 普通用户 1: 管理员,2: 超级管理员