import asyncio
import json
import logging

import pytest

import backend.agents.memory.memory_manager as mm
from backend.agents.memory.memory_manager import DIGEST_EVERY, MemoryManager
from backend.agents.memory.short_term_memory import MemoryUnit, ShortTermMemory
from backend.dao.memory_mapper import DigestState

USER, SESSION = 7, 7


class FakeMapper:
    """内存版的记忆表：行为与 MemoryMapper 一致，包括 fingerprint 去重"""

    def __init__(self, fail: bool = False):
        self.records: list[dict] = []
        self.digests: dict[tuple[int, int], DigestState] = {}
        self.fail = fail

    async def add_record(self, user_id, session_id, user_text, model_text, fingerprint) -> bool:
        if self.fail:
            raise RuntimeError("数据库写入失败")
        if any(r["fingerprint"] == fingerprint for r in self.records):
            return False
        self.records.append({
            "id": len(self.records) + 1, "user_id": user_id, "session_id": session_id,
            "user_text": user_text, "model_text": model_text, "fingerprint": fingerprint,
        })
        return True

    def _of_session(self, user_id, session_id) -> list[dict]:
        return [r for r in self.records if r["user_id"] == user_id and r["session_id"] == session_id]

    async def list_records(self, user_id, session_id, limit) -> list[dict]:
        return self._of_session(user_id, session_id)[-limit:]

    async def count_since(self, user_id, session_id, after_id) -> tuple[int, int]:
        rows = self._of_session(user_id, session_id)
        return len([r for r in rows if r["id"] > after_id]), (rows[-1]["id"] if rows else 0)

    async def get_digest(self, user_id, session_id):
        return self.digests.get((user_id, session_id))

    async def save_digest(self, user_id, session_id, summary, covered_until_id) -> None:
        self.digests[(user_id, session_id)] = DigestState(summary, covered_until_id)


@pytest.fixture
def manager(redis_test_client, monkeypatch):
    stm = ShortTermMemory(max_memory_size=2)
    monkeypatch.setattr(stm, "_client", redis_test_client)

    async def fake_digest(records):
        return f"摘要：共{len(records)}条"

    monkeypatch.setattr(mm, "build_session_digest", fake_digest)

    mgr = MemoryManager.__new__(MemoryManager)
    mgr.long_term_memory = None
    mgr.short_term_memory = stm
    mgr.memory_mapper = FakeMapper()
    mgr._tasks = set()
    return mgr


async def _talk(manager, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        await manager.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))


async def _fill_pending(manager, n: int) -> None:
    """直接写短期记忆，让 pending 里积压 n 条（max=2，第 3 条起每条挤出一条），不触发后台归档"""
    for i in range(n + 2):
        await manager.short_term_memory.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))


async def test_below_limit_spawns_nothing(manager):
    await _talk(manager, 1)
    assert manager._tasks == set()
    assert manager.memory_mapper.records == []


async def test_overflow_archives_original_text_in_background(manager):
    await _talk(manager, 3)
    await manager.shutdown(5)

    [record] = manager.memory_mapper.records
    assert (record["user_text"], record["model_text"]) == ("问题0", "回答0"), "存的是原文，不是摘要"
    assert record["user_id"] == USER and record["session_id"] == SESSION
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_request_path_does_not_wait_for_archive(manager, monkeypatch):
    async def slow_digest(records):
        await asyncio.sleep(1.0)
        return "慢摘要"
    monkeypatch.setattr(mm, "build_session_digest", slow_digest)

    await _talk(manager, 2)
    start = asyncio.get_running_loop().time()
    await manager.add_memory(USER, SESSION, MemoryUnit("问题2", "回答2"))
    assert asyncio.get_running_loop().time() - start < 0.2, "归档必须在后台跑，不能拖慢用户请求"
    await manager.shutdown(5)


async def test_failed_write_keeps_item_pending(manager):
    manager.memory_mapper.fail = True
    await _talk(manager, 3)
    await manager.shutdown(5)

    [raw] = await manager.short_term_memory.get_pending(USER, SESSION)
    assert json.loads(raw)["memory"]["user_memory"] == "问题0"


async def test_drain_recovers_and_is_idempotent(manager):
    manager.memory_mapper.fail = True
    await _talk(manager, 3)
    await manager.shutdown(5)
    assert len(await manager.short_term_memory.get_pending(USER, SESSION)) == 1

    manager.memory_mapper.fail = False
    assert await manager.drain_pending() == 1
    assert await manager.drain_pending() == 0
    assert len(manager.memory_mapper.records) == 1


async def test_same_item_archived_twice_writes_one_row(manager, redis_test_client):
    """写库成功、ack 失败时会重试同一条：靠 fingerprint 去重，不能出现两行"""
    await _fill_pending(manager, 1)
    [raw] = await manager.short_term_memory.get_pending(USER, SESSION)

    assert await manager._archive_one(USER, SESSION, raw) is True
    await redis_test_client.rpush(ShortTermMemory.pending_key(USER, SESSION), raw)   # 模拟 ack 没成功
    assert await manager._archive_one(USER, SESSION, raw) is True

    assert len(manager.memory_mapper.records) == 1


async def test_digest_is_rebuilt_after_enough_records(manager):
    await _fill_pending(manager, DIGEST_EVERY)
    assert await manager.drain_pending() == DIGEST_EVERY

    digest = await manager.memory_mapper.get_digest(USER, SESSION)
    assert digest is not None, f"攒够 {DIGEST_EVERY} 条就该重算摘要"
    assert digest.summary == f"摘要：共{DIGEST_EVERY}条"
    assert digest.covered_until_id == DIGEST_EVERY, "覆盖到最后一条记录"


async def test_digest_waits_until_enough_records(manager):
    await _fill_pending(manager, DIGEST_EVERY - 1)
    await manager.drain_pending()
    assert await manager.memory_mapper.get_digest(USER, SESSION) is None, "不够条数时不调用大模型"


async def test_digest_is_rebuilt_from_raw_records(manager, monkeypatch):
    """重算摘要时喂给模型的必须是原文，绝不是上一版摘要——否则信息会一轮轮失真"""
    seen: list[list[dict]] = []

    async def spy(records):
        seen.append(records)
        return f"摘要{len(seen)}"
    monkeypatch.setattr(mm, "build_session_digest", spy)

    await _fill_pending(manager, DIGEST_EVERY)
    await manager.drain_pending()
    await _fill_pending(manager, DIGEST_EVERY)          # 再来一批，触发第二次重算
    await manager.drain_pending()

    assert len(seen) == 2
    assert all("user_text" in r for r in seen[1]), "第二次拿到的仍然是原文"
    assert not any("摘要1" in str(r) for r in seen[1]), "上一版摘要不能作为输入"


async def test_digest_failure_does_not_lose_records(manager, monkeypatch, caplog):
    async def boom(records):
        raise RuntimeError("摘要模型超时")
    monkeypatch.setattr(mm, "build_session_digest", boom)

    await _fill_pending(manager, DIGEST_EVERY)
    with caplog.at_level(logging.ERROR):
        assert await manager.drain_pending() == DIGEST_EVERY

    assert len(manager.memory_mapper.records) == DIGEST_EVERY, "原文已经落库，不能因为摘要失败而回滚"
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []
    assert "会话摘要更新失败" in caplog.text


async def test_unparseable_item_is_dropped(manager, redis_test_client):
    await redis_test_client.rpush(ShortTermMemory.pending_key(USER, SESSION), "{不是json")
    assert await manager.drain_pending() == 0
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_finished_tasks_are_released(manager):
    await _talk(manager, 4)
    await manager.shutdown(5)
    assert manager._tasks == set()
    assert len(manager.memory_mapper.records) == 2


async def test_background_exception_is_logged(manager, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("意料之外的错误")
    monkeypatch.setattr(manager, "_archive", boom)

    with caplog.at_level(logging.ERROR):
        await _talk(manager, 3)
        await manager.shutdown(5)
    assert "意料之外的错误" in caplog.text
    assert manager._tasks == set()


async def test_shutdown_timeout_cancels_and_waits(manager, monkeypatch):
    async def forever(records):
        await asyncio.sleep(30)
    monkeypatch.setattr(mm, "build_session_digest", forever)

    await _fill_pending(manager, DIGEST_EVERY)
    task = manager._spawn(manager.drain_pending())   # 后台补做：原文写完后卡在摘要那一步
    await asyncio.sleep(0.2)
    await manager.shutdown(timeout=0.05)

    assert task.done() and task.cancelled()
    assert manager._tasks == set()
    assert len(manager.memory_mapper.records) == DIGEST_EVERY, "原文已经落库，只是摘要没来得及做"


async def test_planner_gets_short_memory_and_digest(manager):
    class FakeProfile:
        async def get_by_user_id(self, user_id):
            return "画像"
    manager.long_term_memory = FakeProfile()

    await _fill_pending(manager, DIGEST_EVERY)
    await manager.drain_pending()
    await manager.add_memory(USER, SESSION, MemoryUnit("最新问题", "最新回答"))

    data = await manager.get_memory_for_planner(USER, SESSION)
    assert data["short_memory"][0]["memory"]["user_memory"] == "最新问题"
    assert data["session_digest"] == f"摘要：共{DIGEST_EVERY}条"
    assert data["long_memory"] == "画像"


async def test_planner_digest_is_empty_for_new_session(manager):
    class FakeProfile:
        async def get_by_user_id(self, user_id):
            return None
    manager.long_term_memory = FakeProfile()
    data = await manager.get_memory_for_planner(USER, 999)
    assert data["session_digest"] == ""
