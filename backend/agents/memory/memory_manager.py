"""
后续还可添加功能
将长期记忆存入RAG知识库中：根据用户输入的问题，整合RAG检索和长短期记忆，返回规划器需要的记忆
"""
import asyncio
import json
import time
from typing import Any,TYPE_CHECKING

from backend.agents.agent.extract_memory_agent import get_extract_memory
from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
if TYPE_CHECKING:
    from backend.agents.memory.vector_store_manager import VectorStoreManager
from backend.core.single_tool import singleMeta
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


class MemoryManager(metaclass=singleMeta):
    def __init__(self,
                 long_term_memory:LongTermMemory,
                 short_term_memory:ShortTermMemory,
                 vector_memory:"VectorStoreManager"):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory
        self.vector_memory = vector_memory
        self._tasks: set[asyncio.Task] = set()

    async def get_memory_for_planner(self, user_id: int, session_id: int) -> dict[str, Any]:
        """获取规划器需要的记忆（短期列表 + 单个长期画像）"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        return {
            "short_memory": short_memory,  # list[dict]
            "long_memory": long_memory,    # UserProfileResponse | None
        }

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit):
        """
        对短期记忆进行修改操作，并检查记忆是否已满
        如果已满，则进行记忆的删除，同时将修改后的记忆添加到长期记忆中
        :param user_id:
        :param session_id:
        :param memory:
        :return:
        """

        evicted = await self.short_term_memory.add_memory(user_id,session_id,memory)
        if not evicted:
            return
        # 有 evicted说明记忆溢出了，需要归档，交给 spawn 后台做归档，不阻塞
        self._spawn(self._archive(user_id,session_id,evicted))

    def _spawn(self,coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        return task

    def _on_task_done(self,task: asyncio.Task):
        """
        标记任务成功,幂等性
        """
        # 需要做的任务：
        # 查看这个任务是否调用完毕，调用exception，如果有结果表明执行完毕
        # 如果任务已经取消，那么就直接返回。
        if task not in set:
            return
        result = task.exception()
        if result is not None:
            logger.error("归档过程失败: %s", result,exc_info=True)
        self._tasks.remove(task)

    async def _archive(self,user_id,session_id,raw_item) -> int:
        """在archive_batch中完成批量归档操作"""
        # 先将 raw_item 反序列化为dict，如果出错，直接跳过，并 log 日志记录
        items: list[dict] = []
        keep = []
        for item in raw_item:
            try:
                items.append(json.loads(item))
                keep.append(item)
            except Exception as e:
                logger.error("_archive_batch归档操作时反序列化失败: %s",e)

        # 序列化成功后对记忆通过LLM进行处理
        if len(items) == 0:
            return 0

        try:
            refined = await get_extract_memory(items)
        except Exception as e:
            logger.error("_archive_batch记忆归档失败，回退到pending中待之后重试: %s", e)
            return 0

        archive_success = 0
        archive_time = int(time.time())
        for row,item in zip(keep,refined):
            metadata = {
                "user_id": user_id,
                "session_id": session_id,
                "tags": ','.join(item['tags']),
                "time_stamp": archive_time
            }
            ok = await self.vector_memory.add_document(item['text'],metadata)
            if ok:
                await self.short_term_memory.ack_archived(user_id,session_id,row)
                archive_success += 1
            else:
                logger.error("_archive_batch写入向量库失败，条目保留在pending中待重试")
        return archive_success

    async def drain_pending(self) -> int:
        """重启恢复之后，将已弹出但未归档的进行归档"""
        keys = await self.short_term_memory.scan_pending_keys()
        total = 0
        for key in keys:
            try:
                user_id,session_id = self.short_term_memory.parse_pending_key(key)
            except (IndexError,ValueError):
                logger.error("无法解析pending key，已跳过: %s",key)
                continue

            # 然后就是归档，通过_archive来执行
            texts = await self.short_term_memory.get_pending(user_id,session_id)
            logger.info("启动恢复：session %s:%s 有 %s 条待归档", user_id, session_id, len(texts))
            # 如果 texts 没有读取出来，那么就在这一层失败，不要抛给上层去处理，不合适
            if not texts:
                continue
            total += await self._archive(user_id,session_id,texts)

        if total:
            logger.info("drain_pending 成功归档: %s 条消息",total)
        return total

    async def shutdown(self, timeout: float = 10.0):
        tasks = list(self._tasks)
        if not tasks:
            return
        logger.info("shutdown等待 %s 个 doc 归档", len(tasks))
        done,pending = await asyncio.wait(fs=tasks,timeout=timeout)
        if pending:
            logger.info("shutdown归档过程中有 %s 个 doc 因超时未归档成功",len(pending))
            for cancel_task in pending:
                cancel_task.cancel()

