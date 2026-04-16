import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from dotenv import load_dotenv
from sqlalchemy.orm.decl_api import declarative_base

load_dotenv()

url = os.getenv("SQL_DATABASE_URL")

SQLALCHEMY_DATABASE_URL = url

if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError(
        f"SQL_DATABASE_URL is empty. Please check {str(_env_path)} (backend/.env)."
    )

# 兼容：如果配置了同步驱动 pymysql，但代码使用的是 create_async_engine，
# 会导致 "asyncio extension requires an async driver to be used"。
# 目前 requirements.txt 已安装 asyncmy，因此这里自动替换为 mysql+asyncmy。
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("mysql+pymysql://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace(
        "mysql+pymysql://",
        "mysql+asyncmy://",
        1,
    )

# 创建异步引擎（管理连接池）
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=True,  # 打印 SQL 语句（开发开启，生产关闭）
    pool_pre_ping=True,  # 自动校验连接有效性
    pool_size=10,  # 连接池大小（生产按需调整）
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不失效 ORM 对象
    autoflush=False,
    autocommit=False,
)#type:ignore

# ORM 模型基类
Base = declarative_base()

# 依赖函数：获取异步数据库会话（自动开闭连接）
async def get_db() -> AsyncSessionLocal:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()