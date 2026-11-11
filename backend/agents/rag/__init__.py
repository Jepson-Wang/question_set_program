"""
RAG 子系统。

Task 0 交付的公共接口集中在这里：配置对象与重排接口的默认值。
"""
from backend.agents.rag.config import (
    DEFAULT_RERANK_MODEL,
    DEFAULT_RERANK_URL,
    RagSettings,
)

__all__ = [
    "RagSettings",
    "DEFAULT_RERANK_URL",
    "DEFAULT_RERANK_MODEL",
]
