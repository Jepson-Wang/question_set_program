"""
前缀 + 规范化文本的 sha1 前 16 位，同一道题 id 永远相同，所以重复入库天然幂
"""

import hashlib
from dataclasses import dataclass, asdict, field

from pydantic import BaseModel, Field, field_validator
import unicodedata



GRADE_ORDER = (
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
    "七年级", "八年级", "九年级", "高一", "高二", "高三",
)
_GRADE_ALIASES = {"初一": "七年级", "初二": "八年级", "初三": "九年级"}

DIFFICULTIES = ("简单", "中等", "困难")
KP_SEPARATOR = "|"

def normalize_text(text: str | None) -> str:
    """
    将全角字符转化为半角字符，
    去掉空格，大写转化为小写以及输入空值时不会报错
    """
    if text is not None:
        refined = unicodedata.normalize("NFKC", text)
        result = ''.join(refined.split()).lower()
        return result
    return ""


def content_id(text: str, prefix: str) -> str:
    """
    把输入的文字和前缀改成前缀 + 规范化后文字的 sha1 十六进制摘要的前 16 位
    """
    refined = normalize_text(text).encode("utf-8")
    result = hashlib.sha1(refined).hexdigest()[:16]
    return f"{prefix}{result}"

def grade_rank(grade: str | None) -> int | None:
    """
    返回年级在 GRADE_ORDER 里的位置（从 0 开始）；认识"初一"这类别名；
    认不出、空字符串、None 都返回 None。
    """
    if grade:
        refined = grade.strip()
        result = _GRADE_ALIASES.get(refined, refined)
        return GRADE_ORDER.index(result) if result in GRADE_ORDER else None
        #等价于result = _GRADE_ALIASES.get(refined) if result is
        # None:result = refined
    return None

def _clean_list(texts) -> list[str]:
    """
    把任意输入整理成干净的字符串列表：去首尾空格、丢掉空项、去重且保持原顺序
    """
    if texts is None:
        return []
    if isinstance(texts, str):
        texts = [texts]
    result = list()
    for text in texts:
        refined = str(text).strip()
        if refined == "":
            continue
        if refined not in result:
            result.append(refined)
    return result

def _split_knowledge_points(texts) -> list[str]:
    """
    用竖线拼起来的字符串 → 列表	空字符串或 None 返回空列表；拆出来的空项丢掉
    """
    if texts is None or texts =="":
        return []
    refined = texts.split(KP_SEPARATOR)
    result = list()
    for text in refined:
        if text :
            result.append(text)
    return result

def _blank_to_None(text):
    """
    任意值 → 原值或 None	字符串去空格后为空就返回 None；非字符串原样返回
    """
    if isinstance(text, str) and text.strip() == "":
        return None
    else:
        return text

class QuestionItem(BaseModel):
    stem: str = Field(min_length=1)
    answer:str = Field(min_length=1)
    analysis:str = ""
    question_type:str = "未知"
    difficulty:str = "中等"
    knowledge_points:list[str] = Field(default_factory=list)
    grade:str|None = None
    source_type:str = "pdf"#"pdf" 或"docx" 或"llm"
    source_ref:str = ""

    @field_validator("stem","answer",mode = "before")
    @classmethod
    def _strip(cls, value):
        if isinstance(value,str):
           return  value.strip()
        return value

    @field_validator("analysis","source_ref",mode = "before")
    @classmethod
    def _none_to_empty(cls,value):
        return "" if value is None else value

    @field_validator("question_type", mode="before")
    @classmethod
    def _default_question_type(cls,value):
        return value.strip() if isinstance(value,str) and value.strip() != "" else "未知"

    @field_validator("difficulty", mode="before")
    @classmethod
    def _is_difficulty(cls,value):
        return value if value in DIFFICULTIES else "中等"

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _use_clean_list(cls,value):
        return _clean_list(value)

    @field_validator("grade", mode="before")
    @classmethod
    def _grade_blank_to_None(cls,value):
        return _blank_to_None(value)

    @property
    def id(self) ->str:
        return content_id(self.stem,"q_")

    def to_metadata(self) ->dict:
        return {
            "answer": self.answer,
            "analysis": self.analysis,
            "question_type": self.question_type,
            "difficulty": self.difficulty,
            "knowledge_points": KP_SEPARATOR.join(self.knowledge_points),
            "grade": "" if self.grade is None else self.grade,
            "source_type": self.source_type,
            "source_ref": self.source_ref
        }

    @classmethod
    def from_record(cls,document:str, metadata:dict) -> "QuestionItem":
        return cls(
            stem = document,
            answer=metadata.get("answer", ""),
            analysis = metadata.get("analysis",""),
            question_type = metadata.get("question_type","未知"),
            difficulty = metadata.get("difficulty","中等"),
            knowledge_points = _split_knowledge_points(metadata.get("knowledge_points","")),
            grade = metadata.get("grade") or None,
            source_type = metadata.get("source_type","pdf"),
            source_ref = metadata.get("source_ref", ""),
        )

class KnowledgeChunk(BaseModel):
    text:str = Field(min_length=1)
    knowledge_points:list[str] = Field(default_factory=list)
    grade:str|None = None
    source_ref:str =""

    @field_validator("text", mode="before")
    @classmethod
    def _use_strip_list(cls, value):
        return value.strip() if isinstance(value,str) else value

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _use_clean_list(cls, value):
        return _clean_list(value)

    @field_validator("grade", mode="before")
    @classmethod
    def _use_blank_to_None(cls,value):
        return  _blank_to_None(value)

    @field_validator("source_ref", mode="before")
    @classmethod
    def _none_to_empty(cls,value):
        return "" if value is None else value

    @property
    def id(self) -> str:
        return content_id(self.text, "k_")

    def to_metadata(self) -> dict:
            return {
                "knowledge_points": KP_SEPARATOR.join(self.knowledge_points),
                "grade": "" if self.grade is None else self.grade,
                "source_ref": self.source_ref
            }

    @classmethod
    def from_record(cls,document:str, metadata:dict) -> "KnowledgeChunk":
        return cls(
            text = document,
            knowledge_points = _split_knowledge_points(metadata.get("knowledge_points","")),
            grade = metadata.get("grade") or None,
            source_ref = metadata.get("source_ref", ""),
        )

@dataclass
class JudgeResult:
    passed:bool
    score:float
    reasons:list[str] = field(default_factory=list)
    judge_answer:str = ""
    judge_model:str = ""

    def to_dict(self) ->dict:
        return asdict(self)
@dataclass
class RagQuery:
    text:str
    knowledge_points:list[str] = field(default_factory=list)
    difficulty:str|None = None
    grade:str|None = None

@dataclass
class RetrievedQuestion:
    item:QuestionItem
    score:float|None # 重排分；重排不可用时为融合分
    routes:list[str] # 命中这道题的召回路

@dataclass
class RagContext:
    questions:list[RetrievedQuestion] = field(default_factory=list)
    knowledge:list[KnowledgeChunk] = field(default_factory=list)
    degraded:list[str] = field(default_factory=list)
    timings_ms:dict[str,float] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.questions and not self.knowledge

