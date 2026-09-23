import pytest
from pydantic import ValidationError

from backend.agents.rag.models import (
    KnowledgeChunk,
    QuestionItem,
    RagContext,
    content_id,
    grade_rank,
    normalize_text,
)

def test_normalize_fullwidth_and_whitespace():
    assert normalize_text("解方程 ２x ＋ 3 = 7") == normalize_text("解方程2x+3=7")

def test_content_id_is_stable_and_prefixed():
    a = content_id("解方程 2x+3=7","q_")
    assert a == content_id("解方程2x+3=7","q_")
    assert a.startswith("q_") and len(a) == 18
    assert a != content_id("解方程2x+3=8","q_")

@pytest.mark.parametrize("grade,expected",[("一年级",0),
        ("七年级",6),("初一",6),("高三",11),("大一",None),(None,None),("",None)])
def test_grade_rank(grade, expected):
    assert grade_rank(grade) == expected

def _item(**overrides):
    data = {"stem": "解方程 2x+3=7", "answer": "x=2",
            "knowledge_points": ["一元一次方程"], "grade": "七年级"}
    data.update(overrides)
    return QuestionItem(**data)

def test_blank_stem_is_rejected():
    with pytest.raises(ValidationError):
        _item(stem = "   ")

def test_llm_nulls_fall_back_to_defaults():
    item = _item(difficulty=None, question_type=None, analysis=None, grade="  ")
    assert ((item.difficulty,item.question_type,item.analysis,item.grade) ==
            ("中等","未知","",None))

def test_invalid_difficulty_falls_back_to_medium():
    assert _item(difficulty = "超难").difficulty == "中等"

def  test_knowledge_points_are_stripped_and_deduplicated():
    item = _item(knowledge_points = [" 一元一次方程 ","一元一次方程","","移项"])
    assert item.knowledge_points == ["一元一次方程","移项"]

def test_metadata_is_scalar_only():
    meta = _item(grade=None).to_metadata()
    assert all(isinstance(v,(str,int,float,bool)) for v in meta.values())

def test_question_roundtrip_through_record():
    item = _item(analysis="移项得2x+4",difficulty="简单",question_type = "计算题",
                 source_type="docx", source_ref="练习册.docx#p2")
    restored = QuestionItem.from_record(item.stem,item.to_metadata())
    assert restored == item
    assert restored.id == item.id

def test_question_without_grade_roundtrips_as_none():
    item = _item(grade=None)
    assert QuestionItem.from_record(item.stem,item.to_metadata()).grade is None

def test_knowledge_chunk_roundtrip():
    chunk = KnowledgeChunk(text="含有一个未知数、未知数的次数是 1 的方程叫一元一次方程。",
         knowledge_points=["一元一次方程"], grade="七年级", source_ref="教材.pdf#p3")
    assert KnowledgeChunk.from_record(chunk.text,chunk.to_metadata()) == chunk
    assert chunk.id.startswith("k_")

def test_empty_context():
    assert RagContext().empty