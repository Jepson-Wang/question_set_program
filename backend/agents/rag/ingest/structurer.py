"""
LLM 结构化：页面文本 → QuestionItem / KnowledgeChunk。

按滑动窗口处理：每次读 window 页，相邻窗口重叠一页，跨页的题目至少会在某个窗口里完整出现。
重叠带来的重复靠 id 去重；同一道题在两个窗口里被转写得略有不同（哈希不同）的情况，
交给后面的近似去重兜住。
"""
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from backend.agents.rag.ingest.loaders import PageText
from backend.agents.rag.llm_json import parse_json_object
from backend.agents.rag.models import KnowledgeChunk, QuestionItem
from backend.agents.skills import load_skill
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

SKILL_NAME = "rag_structuring"


@dataclass
class StructureResult:
    questions: list[QuestionItem] = field(default_factory=list)
    knowledge: list[KnowledgeChunk] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def make_windows(pages: list[PageText], window: int = 2) -> list[list[PageText]]:
    """
    步长 window-1，相邻窗口重叠一页；保证最后一页一定被覆盖。

    重叠一页是刻意的：一道题可能题干在第 3 页、答案在第 4 页，只有把这两页放进同一个窗口，
    模型才拿得到完整的题。不重叠的话它会被切成两半，两半都不合格，整道题就丢了。
    """
    if not pages:
        return []
    if len(pages) <= window:
        return [pages]
    step = max(1, window - 1)
    windows = [pages[start:start + window] for start in range(0, len(pages) - window + 1, step)]
    if windows[-1][-1].number != pages[-1].number:
        # 步长整除不了时最后几页会落在所有窗口之外，补一个贴着尾部的窗口，别把最后一页丢掉
        windows.append(pages[-window:])
    return windows


class Structurer:
    """把逐页文本结构化成题目与知识讲解。"""

    def __init__(self, llm, window: int = 2):
        self._llm = llm
        self._window = window

    async def structure(self, pages: list[PageText], source_ref: str, source_type: str,
                        grade_hint: str | None = None) -> StructureResult:
        result = StructureResult()
        seen_questions: set[str] = set()
        seen_knowledge: set[str] = set()
        system = load_skill(SKILL_NAME)

        for win in make_windows(pages, self._window):
            label = f"{source_ref} 第{win[0].number}-{win[-1].number}页"
            # 每页都带【第N页】标记：模型才知道哪道题跨了页，才敢判断自己看到的题干是不是被截断了
            text = "\n\n".join(f"【第{p.number}页】\n{p.text}" for p in win)
            prompt = f"【年级提示】{grade_hint or '无'}\n【待整理文本】\n{text}"
            try:
                response = await self._llm.ainvoke([SystemMessage(content=system), HumanMessage(content=prompt)])
            except Exception as e:
                # 一个窗口失败不能让整份资料作废：记进 errors，继续处理下一个窗口
                logger.warning("结构化窗口失败：%s：%s", label, e)
                result.errors.append(f"{label}：LLM 调用失败（{e}）")
                continue

            data = parse_json_object(getattr(response, "content", None))
            if data is None:
                logger.warning("结构化窗口失败：%s：模型输出不是合法的 JSON 对象", label)
                result.errors.append(f"{label}：输出不是合法的 JSON 对象")
                continue

            ref = f"{source_ref}#p{win[0].number}"
            for raw in data.get("questions") or []:
                try:
                    item = QuestionItem(**{
                        **raw,
                        # 原文给了年级就用原文的，没给才退回调用方的年级提示：原文更具体
                        "grade": raw.get("grade") or grade_hint,
                        "source_type": source_type,
                        "source_ref": ref,
                    })
                except (ValidationError, TypeError, AttributeError):
                    # 缺答案、字段类型不对这类问题只影响这一条，不能让整批白跑
                    logger.warning("跳过一道字段不完整的题：%s", label)
                    result.errors.append(f"{label}：有一道题字段不完整或格式不对，已跳过")
                    continue
                if item.id not in seen_questions:
                    # 重叠窗口里的同一道题 id 相同，只留一份；转写有出入导致 id 不同的，
                    # 交给后面的近似去重兜底
                    seen_questions.add(item.id)
                    result.questions.append(item)

            for raw in data.get("knowledge") or []:
                try:
                    chunk = KnowledgeChunk(**{**raw, "grade": raw.get("grade") or grade_hint, "source_ref": ref})
                except (ValidationError, TypeError, AttributeError):
                    logger.warning("跳过一段格式不对的讲解：%s", label)
                    result.errors.append(f"{label}：有一段讲解格式不对，已跳过")
                    continue
                if chunk.id not in seen_knowledge:
                    seen_knowledge.add(chunk.id)
                    result.knowledge.append(chunk)
        return result
