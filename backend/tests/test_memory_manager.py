import asyncio
import json

import pytest_asyncio

from backend.agents.memory.memory_manager import MemoryManager
from backend.agents.memory.short_term_memory import get_short_term_memory, ShortTermMemory, MemoryUnit
from backend.schemas.request.user_request import UserRequest

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

@pytest_asyncio.fixture
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

    import backend.agents.memory.memory_manager as mm
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
    import backend.agents.memory.memory_manager as mm

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
    manager._tasks = []
    recovered = await manager.drain_pending()

    assert recovered == 1
    assert len(manager.vector_memory.docs) == 1
    assert await manager.short_term_memory.get_pending(USER,SESSION) == []

    assert await manager.drain_pending() == 0
    assert len(manager.vector_memory.docs) == 1