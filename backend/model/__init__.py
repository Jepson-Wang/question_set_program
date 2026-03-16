import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

url = os.getenv("SQL_DATABASE_URL")

SQLALCHEMY_DATABASE_URL = url

# 创建异步引擎（管理连接池）
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=True,  # 打印 SQL 语句（开发开启，生产关闭）
    pool_pre_ping=True,  # 自动校验连接有效性
    pool_size=10,  # 连接池大小（生产按需调整）
)

# 异步会话工厂
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不失效 ORM 对象
    autoflush=False,
    autocommit=False,
)#type:ignore

# ORM 模型基类
Base = declarative_base()

# 依赖函数：获取异步数据库会话（自动开闭连接）
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()