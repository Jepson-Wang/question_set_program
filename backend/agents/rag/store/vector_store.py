"""
向量存储。入库与检索代码只依赖 VectorStore 协议：

- InMemoryVectorStore：纯 Python 实现，测试与离线评测用
- LlamaIndexVectorStore（llama_store.py）：生产实现，经 LlamaIndex 读写 Chroma。
  放在单独的模块里，只用内存实现的代码就不必导入 LlamaIndex 和 chromadb
"""
from dataclasses import dataclass
from typing import Protocol, Sequence

from backend.agents.rag.store.embedder import dot


@dataclass
class VectorHit:
    id: str
    document: str
    metadata: dict
    similarity: float  # 余弦相似度


class VectorStore(Protocol):
    async def upsert(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]],
                     documents: Sequence[str], metadatas: Sequence[dict]) -> None: ...

    async def query(self, embedding: Sequence[float], top_k: int) -> list[VectorHit]: ...

    async def get_all(self) -> list[tuple[str, str, dict]]: ...

    async def count(self) -> int: ...

    async def delete(self, ids: Sequence[str]) -> None: ...


class InMemoryVectorStore:
    def __init__(self):
        self._rows: dict[str, tuple[list[float], str, dict]] = {}

    async def upsert(self, ids, embeddings, documents, metadatas) -> None:
        for row_id, vec, doc, meta in zip(ids, embeddings, documents, metadatas, strict=True):
            self._rows[row_id] = (list(vec), doc, dict(meta))

    async def query(self, embedding, top_k) -> list[VectorHit]:
        hits = [
            VectorHit(row_id, doc, dict(meta), dot(embedding, vec))
            for row_id, (vec, doc, meta) in self._rows.items()
        ]
        hits.sort(key=lambda h: (-h.similarity, h.id))
        return hits[:top_k]

    async def get_all(self) -> list[tuple[str, str, dict]]:
        return [(row_id, doc, dict(meta)) for row_id, (_, doc, meta) in self._rows.items()]

    async def count(self) -> int:
        return len(self._rows)

    async def delete(self, ids) -> None:
        for row_id in ids:
            self._rows.pop(row_id, None)
