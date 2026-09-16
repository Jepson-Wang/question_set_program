from backend.core.executors import shutdown_executors
from backend.middleware.logging import get_logger
from backend.model import engine, Base
from backend.utils.redis_client import close_redis

logger = get_logger(__name__)


def _get_memory_manager():
    """延迟导入：agent_api 在导入时才完成记忆模块的实例化。"""
    from backend.api.user_api.agent_api import memory_manager
    return memory_manager


async def startup_event():
    """应用启动：建表 + 补做上次未完成的记忆归档"""
    logger.info("应用启动中，正在创建数据库表...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表创建完成")

    try:
        recovered = await _get_memory_manager().drain_pending()
        logger.info("记忆归档恢复完成，补做 %s 条", recovered)
    except Exception as e:
        # 恢复失败不应阻止服务启动；条目留在 pending，下次再试
        logger.error("记忆归档恢复失败：%s", e, exc_info=True)


async def shutdown_event():
    """应用关闭：先等在途归档做完，再释放线程池与连接"""
    logger.info("应用关闭中，等待在途记忆归档...")
    try:
        await _get_memory_manager().shutdown(timeout=10.0)
    except Exception as e:
        logger.error("等待归档任务时出错：%s", e, exc_info=True)

    logger.info("正在释放线程池与数据库连接...")
    shutdown_executors(wait=True)
    await engine.dispose()
    await close_redis()
    logger.info("资源已全部释放")