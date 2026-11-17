"""
第三方模型复核（入库闸门）。

三个关键设计：
1. 先让裁判独立解题，再与给定答案比对——而不是问它「这个答案对不对」，
   后一种问法会诱导模型附和给定答案。
2. 失败即拒（fail closed）：裁判调用失败、输出无法解析、布尔项不是真正的 true，
   题目都进隔离区而不是放行。闸门的意义就在于不确定时不放行。
3. 裁判必须与生成模型不同家族：同家族模型会系统性地偏向自己的输出。
"""
import re

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.rag.llm_json import parse_json_object
from backend.agents.rag.models import JudgeResult, QuestionItem
from backend.agents.skills import load_skill

SKILL_NAME = "rag_judge"
REQUIRED_CHECKS = ("answer_consistent", "stem_complete", "within_grade", "knowledge_points_accurate")
_FAMILY_ALIASES = {"moonshot": "kimi", "chatglm": "glm", "tongyi": "qwen"}


class JudgeConfigError(RuntimeError):
    pass


def model_family(model_name: str | None) -> str:
    """
    取模型名开头的字母段作为家族：qwen-plus、qwen3-max -> qwen；glm-5 -> glm；
    deepseek-v3 -> deepseek。这是启发式规则，目的是拦住明显的配置错误。
    """
    match = re.match(r"[a-z]+", (model_name or "").strip().lower())
    family = match.group(0) if match else ""
    return _FAMILY_ALIASES.get(family, family)


def ensure_third_party(judge_model: str | None, generator_model: str | None) -> None:
    if not judge_model:
        raise JudgeConfigError("未配置 JUDGE_MODEL，入库闸门无法工作")
    judge_family = model_family(judge_model)
    if judge_family and judge_family == model_family(generator_model):
        raise JudgeConfigError(
            f"裁判模型 {judge_model} 与生成模型 {generator_model} 属于同一家族（{judge_family}），"
            f"失去了第三方复核的意义"
        )


def build_judge_llm(settings):
    """按独立配置构建裁判模型。配置不完整或与生成模型同家族时抛 JudgeConfigError。"""
    ensure_third_party(settings.judge_model, settings.generator_model)
    if not settings.judge_api_url or not settings.judge_api_key:
        raise JudgeConfigError("未配置 JUDGE_API_URL / JUDGE_API_KEY，入库闸门无法工作")
    # 放在函数内部导入：裁判用的 langchain_openai 只在真正要建闸门时才需要，
    # 不该让没开 RAG 的进程也背上这个依赖的导入开销。
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.judge_model,
        api_key=settings.judge_api_key,
        base_url=settings.judge_api_url,
        temperature=0,
    )


class QuestionJudge:
    def __init__(self, llm, model_name: str, pass_score: float = 0.8):
        self._llm = llm
        self._model_name = model_name
        self._pass_score = pass_score

    def _reject(self, reason: str) -> JudgeResult:
        return JudgeResult(False, 0.0, [reason], judge_model=self._model_name)

    async def judge(self, item: QuestionItem) -> JudgeResult:
        prompt = (
            f"【题干】{item.stem}\n"
            f"【给定答案】{item.answer}\n"
            f"【年级】{item.grade or '未知'}\n"
            f"【标注知识点】{'、'.join(item.knowledge_points) or '无'}"
        )
        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=load_skill(SKILL_NAME)),
                HumanMessage(content=prompt),
            ])
        except Exception as e:
            # 裁判不可用时闸门必须关着：调用失败一律判不通过，进隔离区而不是放行。
            return self._reject(f"裁判调用失败：{e}")

        data = parse_json_object(getattr(response, "content", None))
        if data is None:
            return self._reject("裁判输出无法解析为 JSON")

        # 必须是真正的 true：字符串 "true"、1、缺失都算不通过
        failed = [key for key in REQUIRED_CHECKS if data.get(key) is not True]
        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))

        reasons = [str(r) for r in (data.get("reasons") or []) if str(r).strip()]
        reasons += [f"未通过检查项：{key}" for key in failed]
        if score < self._pass_score:
            reasons.append(f"评分 {score:.2f} 低于通过线 {self._pass_score}")

        return JudgeResult(
            passed=not failed and score >= self._pass_score,
            score=score,
            reasons=reasons,
            judge_answer=str(data.get("judge_answer", "")),
            judge_model=self._model_name,
        )
