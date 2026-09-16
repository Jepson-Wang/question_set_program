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
        # 事件循环对任务只持有弱引用，自己不存一份的话，任务可能跑到一半被垃圾回收
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self,task: asyncio.Task):
        """任务结束（成功、失败或被取消）时调用：释放引用，并把异常记进日志"""
        self._tasks.discard(task)
        # 被取消的任务调用 exception() 会抛 CancelledError，要先排除
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # 回调里没有「当前异常」，exc_info=True 取不到堆栈，要把异常对象直接传进去
            logger.error("归档任务异常退出: %s", exc, exc_info=exc)

    async def _archive(self,user_id,session_id,raw_items: list[str]) -> int:
        """
        逐条归档，返回成功的条数。
        不把多条记录一起交给 LLM 再按顺序配对：模型可能把一条拆成多条，也可能把多条合成一条，
        输出条数和输入对不上时，按顺序配对会 ack 错条目，造成内容丢失或重复写入。
        """
        success = 0
        for raw in raw_items:
            if await self._archive_one(user_id,session_id,raw):
                success += 1
        return success

    async def _archive_one(self,user_id,session_id,raw: str) -> bool:
        """归档一条：精炼出的内容全部写进向量库之后才 ack；任何一步失败，条目都留在 pending 待重试"""
        try:
            unit = json.loads(raw)
        except json.JSONDecodeError as e:
            # 坏数据重试多少次都不会好，直接从 pending 删掉，否则每次启动都会卡在它上面
            logger.error("待归档记忆无法反序列化，已丢弃: %s | %s", e, raw[:100])
            await self.short_term_memory.ack_archived(user_id,session_id,raw)
            return False

        try:
            refined = await get_extract_memory([unit])
        except Exception as e:
            logger.error("记忆精炼失败，留在 pending 待重试: %s", e, exc_info=True)
            return False
        if not refined:
            # 精炼的提示词要求每条记录都有输出，空结果说明模型输出无法解析
            logger.warning("记忆精炼没有产出内容，留在 pending 待重试")
            return False

        archive_time = int(time.time())
        for item in refined:
            metadata = {
                "user_id": user_id,
                "session_id": session_id,
                "tags": ','.join(item['tags']),
                "time_stamp": archive_time
            }
            if not await self.vector_memory.add_document(item['text'],metadata):
                # 前面几条可能已经写进去了，重试时会再写一遍；向量库不按内容去重，这里接受少量重复
                logger.error("写入向量库失败，留在 pending 待重试")
                return False

        await self.short_term_memory.ack_archived(user_id,session_id,raw)
        return True

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
        logger.info("shutdown 等待 %s 个归档任务", len(tasks))
        done,pending = await asyncio.wait(tasks,timeout=timeout)
        if pending:
            logger.warning("shutdown 时有 %s 个归档任务超时，已取消，下次启动由 drain_pending 补做",len(pending))
            for cancel_task in pending:
                cancel_task.cancel()
            # cancel() 只是发出取消请求，要等任务真正退出；否则事件循环关闭时会报 Task was destroyed but it is pending
            await asyncio.gather(*pending, return_exceptions=True)

