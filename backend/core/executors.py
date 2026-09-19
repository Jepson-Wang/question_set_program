"""
集中管理进程级线程池，提供获取与关停入口
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from backend.middleware.logging import get_logger

logger = get_logger(__name__)

VECTOR_POOL_SIZE = 4

_vector_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()

def get_vector_executor() -> ThreadPoolExecutor:
    global _vector_executor
    if _vector_executor is None:
        with _lock:
            if _vector_executor is None:
                _vector_executor = ThreadPoolExecutor(
                    max_workers=VECTOR_POOL_SIZE,
                    thread_name_prefix="vec",
                )
                logger.info("向量库线程池已创建，max_workers=%s",VECTOR_POOL_SIZE)
    return _vector_executor

def shutdown_executors(wait: bool = True) -> None:
    global _vector_executor
    with _lock:
        if _vector_executor is not None:
            _vector_executor.shutdown(wait=wait)
            _vector_executor = None
            logger.info("向量库线程池已关闭")
