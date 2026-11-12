"""
向量化。

所有向量都归一化为单位长度：余弦相似度因此等于点积，
InMemoryVectorStore 与 LlamaIndex + Chroma（cosine 空间）给出的相似度可以直接比较，
去重与防泄漏的阈值在两种实现下含义一致。
"""
import math
from typing import Protocol, Sequence


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return [0.0 for _ in vec]
    return [x / norm for x in vec]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """单位向量的点积即余弦相似度。"""
    return sum(x * y for x, y in zip(a, b))


class LlamaIndexEmbedder:
    """
    包装任意 LlamaIndex BaseEmbedding，输出归一化后的向量。默认用 get_llm.py 里已有的 DashScope 封装。

    按 batch_size 分段、一段一段地等，而不是把全部文本一次交给 aget_text_embedding_batch：
    后者会把所有批次一起 gather，而 get_llm.py 的封装没有实现批量接口、每条文本单独发一个请求——
    不分段的话，入库一份 200 段的教材会同时打出 200 个请求，很容易被限流。
    """

    def __init__(self, model=None, batch_size: int = 10):
        if model is None:
            from backend.agents.agent.embedding import get_embedding_model
            model = get_embedding_model()
        self._model = model
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        texts = list(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start:start + self._batch_size]
            vectors.extend(await self._model.aget_text_embedding_batch(batch))
        return [l2_normalize(v) for v in vectors]
