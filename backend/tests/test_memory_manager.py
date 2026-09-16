import asyncio
import json
import logging

import pytest

import backend.agents.memory.memory_manager as mm
from backend.agents.memory.memory_manager import MemoryManager
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit

USER, SESSION = 7, 7


class FakeVectorStore:
    def __init__(self, should_fail: bool = False):
        self.docs: list[tuple[str, dict]] = []
        self.should_fail = should_fail

    async def add_document(self, text: str, metadata: dict) -> bool:
        if self.should_fail:
            return False
        self.docs.append((text, metadata))
        return True

@pytest.fixture
def manager(redis_test_client,monkeypatch):
    """创建一个manager对象"""
    stm = ShortTermMemory(max_memory_size=2)
    monkeypatch.setattr(target=stm,name="_client",value=redis_test_client)

    vector = FakeVectorStore()

    async def extract_memory(units):
        return [{
            "text": f"精炼:{u['memory']['user_memory']}",
            "tags": ['事实']
        } for u in units]

    monkeypatch.setattr(mm,'get_extract_memory',extract_memory)

    mgr = MemoryManager.__new__(MemoryManager)
    mgr.long_term_memory = None
    mgr.short_term_memory = stm
    mgr.vector_memory = vector
    mgr._tasks = set()
    return mgr

async def test_below_limit_spawns_nothing(manager):
    await manager.add_memory(USER,SESSION,MemoryUnit("问题","回答"))
    assert manager._tasks == set()
    assert manager.vector_memory.docs == []

async def test_overflow_archive_in_background(manager):
    for i in range(3):
        await manager.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))
    await manager.shutdown(timeout = 5)
    assert len(manager.vector_memory.docs) == 1
    text, metadata = manager.vector_memory.docs[0]
    assert text == "精炼:问题0"
    assert metadata["user_id"] == USER
    assert isinstance(metadata['tags'],str),"Chroma 的 metadata 只接受标量"
    assert await manager.short_term_memory.get_pending(USER,SESSION) == []

async def test_request_path_docs_not_wait_for_archive(manager,monkeypatch):
    async def slow_extract(units):
        await asyncio.sleep(1)
        return [{"text": "慢精炼","tags": ["事实"]}]

    monkeypatch.setattr(mm,"get_extract_memory",slow_extract)

    for i in range(2):
        await manager.add_memory(USER,SESSION,MemoryUnit(f'问题{i}',f'回答{i}'))

    start = asyncio.get_running_loop().time()
    await manager.add_memory(USER,SESSION,MemoryUnit(f'问题2',f'回答2'))
    expire = asyncio.get_running_loop().time() - start

    assert expire < 0.5,"归档操作应当是后台运行的，不阻塞用户请求路径"
    await manager.shutdown(timeout=5)

async def test_failed_archive_write_keeps_item_pending(manager):
    """失败的归档操作不会将pending队列中的相应doc给删除"""
    # 也就是说那个pending应该还存在于pending队列中，通过get_pending()可以查询到
    manager.vector_memory.should_fail = True

    for i in range(3):
        await manager.add_memory(USER,SESSION,MemoryUnit(f'问题{i}',f'回答{i}'))

    await manager.shutdown(5)
    # 然后判断 pending 中是否有相应的消息
    result = await manager.short_term_memory.get_pending(USER,SESSION)
    assert len(result) == 1
    assert json.loads(result[0])['memory']['user_memory'] == '问题0'

async def test_drain_recovers_is_idempotent(manager):
    """保证drain_pending只执行一次"""
    manager.vector_memory.should_fail = True

    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))

    await manager.shutdown(5)
    assert len(await manager.short_term_memory.get_pending(USER,SESSION)) == 1

    manager.vector_memory.should_fail = False
    recovered = await manager.drain_pending()

    assert recovered == 1
    assert len(manager.vector_memory.docs) == 1
    assert await manager.short_term_memory.get_pending(USER,SESSION) == []

    assert await manager.drain_pending() == 0
    assert len(manager.vector_memory.docs) == 1


async def _fill_pending(manager, n):
    """直接写短期记忆，让 pending 里积压 n 条（max=2，第 3 条起每条挤出一条），不触发后台归档"""
    for i in range(n + 2):
        await manager.short_term_memory.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))


async def test_finished_tasks_are_released(manager):
    """任务结束后要从 _tasks 里移除，否则每溢出一次就多攒一个"""
    for i in range(4):
        await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))
    await manager.shutdown(5)
    assert manager._tasks == set()
    assert len(manager.vector_memory.docs) == 2


async def test_background_exception_is_logged(manager, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("意料之外的错误")
    monkeypatch.setattr(manager, "_archive", boom)

    with caplog.at_level(logging.ERROR):
        for i in range(3):
            await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))
        await manager.shutdown(5)
    assert "意料之外的错误" in caplog.text
    assert manager._tasks == set()


async def test_one_record_refined_into_several_is_not_lost(manager, monkeypatch):
    """
    模型把每条记录拆成两条事实。按顺序 zip 配对的旧实现里，第 2 条记录会配上第 1 条的第二条事实被 ack，
    它自己的内容永久丢失；逐条归档则每条的产出都完整写入。
    """
    async def split(units):
        q = units[0]['memory']['user_memory']
        return [{"text": f"{q}-事实A", "tags": ["事实"]}, {"text": f"{q}-事实B", "tags": ["事实"]}]
    monkeypatch.setattr(mm, "get_extract_memory", split)

    await _fill_pending(manager, 2)
    assert await manager.drain_pending() == 2
    assert sorted(t for t, _ in manager.vector_memory.docs) == ["问题0-事实A", "问题0-事实B", "问题1-事实A", "问题1-事实B"]
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_items_are_archived_independently(manager, monkeypatch):
    """一条精炼失败不影响另一条：成功的照常 ack，失败的留在 pending"""
    async def flaky(units):
        if units[0]['memory']['user_memory'] == '问题0':
            raise RuntimeError("LLM 超时")
        return [{"text": "精炼", "tags": ["事实"]}]
    monkeypatch.setattr(mm, "get_extract_memory", flaky)

    await _fill_pending(manager, 2)
    assert await manager.drain_pending() == 1
    [left] = await manager.short_term_memory.get_pending(USER, SESSION)
    assert json.loads(left)['memory']['user_memory'] == '问题0'


async def test_unparseable_item_is_dropped(manager, redis_test_client):
    """坏数据重试也不会好，要从 pending 删掉，不能每次启动都卡在它上面"""
    await redis_test_client.rpush(ShortTermMemory.pending_key(USER, SESSION), "{不是json")
    assert await manager.drain_pending() == 0
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_shutdown_timeout_cancels_and_waits(manager, monkeypatch):
    """超时的任务被取消，并且 shutdown 返回前已经真正退出；条目留在 pending 等下次启动补做"""
    async def forever(units):
        await asyncio.sleep(30)
    monkeypatch.setattr(mm, "get_extract_memory", forever)

    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))
    [task] = manager._tasks
    await manager.shutdown(timeout=0.1)

    assert task.done() and task.cancelled()
    assert manager._tasks == set()
    assert len(await manager.short_term_memory.get_pending(USER, SESSION)) == 1

async def test_shutdown_waits_for_archive_before_closing(monkeypatch):
    """关停顺序：先归档，再关线程池于连接"""
    from backend.core import hooks

    calls = []

    class FakeManager:
        async def shutdown(self,timeout):
            calls.append("archive")

    class FakeEngine:
        async def dispose(self):
            calls.append("engine")

    async def fake_close_redis():
        calls.append("redis")

    monkeypatch.setattr(hooks,"_get_memory_manager",lambda: FakeManager())
    monkeypatch.setattr(hooks,"shutdown_executors",lambda wait = True:calls.append("executors"))
    monkeypatch.setattr(hooks,"engine",FakeEngine())
    monkeypatch.setattr(hooks,"close_redis",fake_close_redis)

    await hooks.shutdown_event()

    assert calls[0] == "archive", f"必须最先等归档，实际顺序：{calls}"
    assert set(calls) == {"archive", "executors", "engine", "redis"}