"""
所有目录都相对 backend/ 解析，不相对当前工作目录，所以不管从哪里启动，读写位置都一样。
"""
import os
from dataclasses import dataclass
from pathlib import Path

from backend.core.config import BACKEND_ROOT, load_env

load_env()

# 以 Task 0 探针的实际请求结果为准
DEFAULT_RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
DEFAULT_RERANK_MODEL = "gte-rerank-v2"


def _resolve_dir(value: str | None, default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RagSettings:
    enabled: bool
    db_dir: Path
    upload_dir: Path
    eval_dir: Path
    rerank_url: str
    rerank_model: str
    rerank_api_key: str | None
    judge_api_url: str | None
    judge_api_key: str | None
    judge_model: str | None
    generator_model: str
    # 检索
    route_top_k: int = 20
    fusion_top_n: int = 30
    final_top_k: int = 3
    knowledge_top_k: int = 2
    rrf_k: int = 60
    rerank_min_score: float = 0.3
    knowledge_min_similarity: float = 0.5
    leak_threshold: float = 0.95
    context_max_chars: int = 1200
    # 入库
    near_dup_threshold: float = 0.95
    judge_pass_score: float = 0.8
    judge_concurrency: int = 4
    max_upload_mb: int = 20

    @classmethod
    def from_env(cls) -> "RagSettings":
        return cls(
            enabled=_env_bool("RAG_ENABLED", True),
            db_dir=_resolve_dir(os.getenv("RAG_DB_DIR"), "rag_db"),
            upload_dir=_resolve_dir(os.getenv("RAG_UPLOAD_DIR"), "rag_uploads"),
            eval_dir=_resolve_dir(os.getenv("RAG_EVAL_DIR"), "rag_eval"),
            rerank_url=os.getenv("RERANK_API_URL") or DEFAULT_RERANK_URL,
            rerank_model=os.getenv("RERANK_MODEL") or DEFAULT_RERANK_MODEL,
            rerank_api_key=os.getenv("RERANK_API_KEY") or os.getenv("API_KEY"),
            judge_api_url=os.getenv("JUDGE_API_URL"),
            judge_api_key=os.getenv("JUDGE_API_KEY"),
            judge_model=os.getenv("JUDGE_MODEL"),
            generator_model=os.getenv("MODEL_NAME", "glm-5"),
            judge_pass_score=float(os.getenv("RAG_JUDGE_PASS_SCORE", "0.8")),
        )
