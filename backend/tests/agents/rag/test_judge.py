import dataclasses
import json

import pytest

from backend.agents.rag.ingest.judge import (
    REQUIRED_CHECKS,
    JudgeConfigError,
    QuestionJudge,
    build_judge_llm,
    ensure_third_party,
    model_family,
)
from backend.agents.rag.models import QuestionItem
from backend.tests.agents.rag.helpers import ScriptedLLM

ITEM = QuestionItem(stem="解方程 2x+3=7", answer="x=2", grade="七年级", knowledge_points=["一元一次方程"])


def _verdict(**overrides):
    data = {
        "judge_answer": "x=2", "answer_consistent": True, "stem_complete": True,
        "within_grade": True, "knowledge_points_accurate": True, "score": 0.9, "reasons": [],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize("name, family", [
    ("qwen-plus", "qwen"), ("qwen3-max", "qwen"), ("glm-5", "glm"),
    ("deepseek-v3", "deepseek"), ("moonshot-v1-8k", "kimi"), ("", ""),
])
def test_model_family(name, family):
    assert model_family(name) == family


def test_same_family_judge_is_refused():
    with pytest.raises(JudgeConfigError, match="同一家族"):
        ensure_third_party("qwen3-max", "qwen-plus")


def test_missing_judge_model_is_refused():
    with pytest.raises(JudgeConfigError, match="JUDGE_MODEL"):
        ensure_third_party(None, "qwen-plus")


def test_build_judge_llm_requires_endpoint_and_key(rag_settings):
    with pytest.raises(JudgeConfigError, match="JUDGE_API_URL"):
        build_judge_llm(dataclasses.replace(rag_settings, judge_api_key=None))


def test_build_judge_llm_accepts_a_third_party_model(rag_settings):
    assert build_judge_llm(rag_settings) is not None


async def test_passes_when_every_check_is_true():
    result = await QuestionJudge(ScriptedLLM(_verdict()), "deepseek-v3").judge(ITEM)
    assert result.passed and result.score == 0.9
    assert result.judge_answer == "x=2" and result.judge_model == "deepseek-v3"


async def test_any_failed_check_rejects():
    result = await QuestionJudge(ScriptedLLM(_verdict(answer_consistent=False, reasons=["答案应为 x=2"])),
                                 "deepseek-v3").judge(ITEM)
    assert not result.passed
    assert "答案应为 x=2" in result.reasons
    assert "未通过检查项：answer_consistent" in result.reasons


async def test_low_score_rejects():
    result = await QuestionJudge(ScriptedLLM(_verdict(score=0.5)), "deepseek-v3", pass_score=0.8).judge(ITEM)
    assert not result.passed
    assert any("低于通过线" in r for r in result.reasons)


async def test_string_true_is_not_true():
    """布尔检查必须是真正的 true，模型输出 "true" 字符串也要拒——宁可错杀。"""
    result = await QuestionJudge(ScriptedLLM(_verdict(within_grade="true")), "deepseek-v3").judge(ITEM)
    assert not result.passed


async def test_unparseable_output_fails_closed():
    result = await QuestionJudge(ScriptedLLM("这道题没问题，通过。"), "deepseek-v3").judge(ITEM)
    assert not result.passed and "无法解析" in result.reasons[0]


async def test_llm_failure_fails_closed():
    result = await QuestionJudge(ScriptedLLM(TimeoutError("超时")), "deepseek-v3").judge(ITEM)
    assert not result.passed and "裁判调用失败" in result.reasons[0]


async def test_judge_is_asked_to_solve_before_comparing():
    llm = ScriptedLLM(_verdict())
    await QuestionJudge(llm, "deepseek-v3").judge(ITEM)
    system, human = llm.calls[0]
    assert "先独立解题" in system.content
    assert "【题干】解方程 2x+3=7" in human.content and "【给定答案】x=2" in human.content


async def test_number_one_is_not_true():
    """1 也不是 true：Python 里 `1 is True` 为假，用 `is` 判定才能挡住这类输出。"""
    result = await QuestionJudge(ScriptedLLM(_verdict(stem_complete=1)), "deepseek-v3").judge(ITEM)
    assert not result.passed
    assert "未通过检查项：stem_complete" in result.reasons


async def test_missing_check_key_is_rejected():
    """检查项整个缺失时不能默认通过——闸门只认明确写出的 true。"""
    payload = json.loads(_verdict())
    del payload["knowledge_points_accurate"]
    result = await QuestionJudge(ScriptedLLM(json.dumps(payload, ensure_ascii=False)), "deepseek-v3").judge(ITEM)
    assert not result.passed
    assert "未通过检查项：knowledge_points_accurate" in result.reasons


async def test_score_exactly_on_the_pass_line_passes():
    result = await QuestionJudge(ScriptedLLM(_verdict(score=0.8)), "deepseek-v3", pass_score=0.8).judge(ITEM)
    assert result.passed and result.score == 0.8


async def test_rejected_result_keeps_the_judge_model():
    """被拒的题也要留下裁判模型名，否则隔离区里查不出是谁判的。"""
    result = await QuestionJudge(ScriptedLLM(_verdict(answer_consistent=False)), "deepseek-v3").judge(ITEM)
    assert not result.passed and result.judge_model == "deepseek-v3"


def test_required_checks_cover_the_four_dimensions():
    assert REQUIRED_CHECKS == (
        "answer_consistent", "stem_complete", "within_grade", "knowledge_points_accurate",
    )


def test_build_judge_llm_refuses_a_same_family_pair(rag_settings):
    with pytest.raises(JudgeConfigError, match="同一家族"):
        build_judge_llm(dataclasses.replace(rag_settings, judge_model="qwen3-max"))


def test_ensure_third_party_accepts_different_families():
    assert ensure_third_party("deepseek-v3", "qwen-plus") is None
