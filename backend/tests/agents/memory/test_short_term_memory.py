import asyncio
import json

import pytest

from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit

USER,SESSION = 1,1

@pytest.fixture
def stm(redis_test_client,monkeypatch):
    """把ShortTermMemory 指向测试库db 15"""
    memory = ShortTermMemory(max_memory_size=3)
    monkeypatch.setattr(memory,"_client",redis_test_client)
    return memory

async def test_newest_first(stm):
    for i in range(3):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    got = await stm.get_latest_memories(USER,SESSION,limit=3)
    assert [m["memory"]["user_memory"] for m in got] == ["问题2","问题1","问题0"]

async def test_no_eviction_below_limit(stm):
    evicted = await stm.add_memory(USER,SESSION,MemoryUnit("问题0","回答0"))
    assert evicted == []
    assert await stm.get_memory_size(USER,SESSION) == 1

async def test_eviction_moves_oldest_to_pending(stm):
    for i in range(3):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    evicted = await stm.add_memory(USER,SESSION,MemoryUnit("问题3","回答3"))
    assert len(evicted) == 1
    assert json.loads(evicted[0])["memory"]["user_memory"] == "问题0"
    assert await stm.get_memory_size(USER,SESSION) == 3
    pending = await stm.get_pending(USER,SESSION)
    assert pending == evicted

async def test_evicted_item_is_invisible_to_readers(stm):
    for i in range(4):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    visible = await stm.get_latest_memories(USER,SESSION,limit=10)
    assert "问题0" not in [m["memory"]["user_memory"] for m in visible]

async def test_concurrent_writes_lose_nothing(stm):
    results = await asyncio.gather(*[
        stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))
        for i in range(20)
    ])

    in_window = await stm.get_latest_memories(USER,SESSION,limit = 100)
    in_pending = await stm.get_pending(USER,SESSION)

    assert len(in_window) == 3,"窗口大小必须严格等于max_memory_size"
    assert len(in_window) + len(in_pending) == 20,"并发写入不会丢失消息"

    evicted_total = sum(len(r) for r in results)
    assert evicted_total == len(in_pending),"每条被淘汰的记忆都应该被返回给调用方一次"

async def test_ack_removes_from_pending(stm):
    for i in range(4):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    pending = await stm.get_pending(USER,SESSION)
    removed = await stm.ack_archived(USER,SESSION,pending[0])

    assert removed == 1
    assert await stm.get_pending(USER,SESSION) == []

async def test_ttl_is_set(stm):
    await stm.add_memory(USER,SESSION,MemoryUnit('问题','回答'))
    ttl = await stm._client.ttl(ShortTermMemory.key(USER,SESSION))
    assert 0 < ttl <= 86400

async def test_scan_pending_keys(stm):
    for i in range(4):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    keys = await stm.scan_pending_keys()
    assert ShortTermMemory.pending_key(USER,SESSION) in keys
    assert ShortTermMemory.parse_pending_key(keys[0]) == (USER,SESSION)