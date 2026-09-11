import asyncio
import threading

from backend.core import executors


def test_vector_executor_is_bounded_and_named():
    ex = executors.get_vector_executor()
    # ThreadPoolExecutor 没有公开的 max_workers 属性，池大小存在 _max_workers 里
    assert ex._max_workers == 4,"必须有界，避免embedding风暴打满线程"
    assert executors.get_vector_executor() is ex,"必须复用同一个池"

async def test_vector_executor_threads_are_prefixed():
    loop = asyncio.get_running_loop()
    name = await loop.run_in_executor(
        executors.get_vector_executor(),lambda: threading.current_thread().name
    )
    assert name.startswith("vec"),f"线程名应该以 vec 开头，实际为{name}"

def test_shutdown_is_idempotent():
    executors.get_vector_executor()
    executors.shutdown_executors()
    executors.shutdown_executors()  #重复执行两次
    # 关闭后再取应该拿到一个可用的新池
    assert executors.get_vector_executor()._max_workers == 4