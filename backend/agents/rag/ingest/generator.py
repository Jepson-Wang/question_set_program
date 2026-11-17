"""
种子题生成：按「知识点 + 年级 + 难度 + 数量」批量生成题目。

生成结果一律视为不可信，后续必须经过第三方复核才能入库。
每次调用最多生成 BATCH_SIZE 道：一次要太多，模型输出会被截断或质量下滑。
"""
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from backend.agents.rag.llm_json import parse_json_array
from backend.agents.rag.models import QuestionItem, normalize_difficulty
from backend.agents.skills import load_skill

SKILL_NAME = "rag_seed_generation"
BATCH_SIZE = 10


class SeedGenerator:
    def __init__(self, llm):
        self._llm = llm

    async def generate(self, knowledge_point: str, grade: str, difficulty: str,
                       n: int) -> tuple[list[QuestionItem], list[str]]:
        """返回 (题目列表, 错误信息列表)。"""
        batch_id = uuid.uuid4().hex[:8]
        system = load_skill(SKILL_NAME)
        items: list[QuestionItem] = []
        errors: list[str] = []

        remaining = n
        while remaining > 0:
            size = min(BATCH_SIZE, remaining)
            remaining -= size
            prompt = f"知识点：{knowledge_point}\n年级：{grade}\n难度：{difficulty}\n数量：{size}"
            try:
                response = await self._llm.ainvoke([SystemMessage(content=system), HumanMessage(content=prompt)])
            except Exception as e:
                errors.append(f"生成调用失败（{e}）")
                continue
            data = parse_json_array(getattr(response, "content", None))
            if data is None:
                errors.append("生成结果不是合法的 JSON 数组")
                continue

            for raw in data[:size]:
                try:
                    kps = list(raw.get("knowledge_points") or [])
                    if knowledge_point not in kps:
                        kps.insert(0, knowledge_point)  # 按指定知识点出的题，必须带上它
                    items.append(QuestionItem(**{
                        **raw,
                        "knowledge_points": kps,
                        # 模型回一个枚举外的难度（比如「较难」）时用请求的难度兜底，
                        # 否则 QuestionItem 的校验器会一律改成「中等」，请求参数就丢了
                        "difficulty": normalize_difficulty(raw.get("difficulty"), difficulty),
                        "grade": raw.get("grade") or grade,
                        "source_type": "llm",
                        "source_ref": f"llm:{batch_id}",
                    }))
                except (ValidationError, TypeError, AttributeError):
                    errors.append("有一道生成的题字段不完整或格式不对，已跳过")
        return items, errors
