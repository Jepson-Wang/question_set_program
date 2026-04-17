from backend.model import engine, Base
from backend.utils.redis_client import close_redis
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


async def startup_event():
    """应用启动时创建数据库表"""
    logger.info("应用启动中，正在创建数据库表...")
    async with engine.begin() as conn:
        # 创建所有已定义的表
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表创建完成")

async def shutdown_event():
    """应用关闭时释放资源"""
    logger.info("应用关闭中，正在释放数据库连接...")
    await engine.dispose()
    await close_redis()
    logger.info("数据库连接已释放")