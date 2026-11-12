"""RAG 测试用的替身与工具。全部离线、结果确定。"""
import hashlib

from backend.agents.rag.models import normalize_text
from backend.agents.rag.store.embedder import l2_normalize


class HashingEmbedder:
    """
    确定性的假 embedding：规范化文本的字符二元组，用 md5 哈希到固定维度计数。
    文本越相似，向量越接近；不联网、不花钱。
    用 md5 而不是内置 hash()：后者每个进程随机加盐，测试结果会不稳定。
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        s = normalize_text(text)
        grams = [s[i:i + 2] for i in range(len(s) - 1)] or [s]
        for gram in grams:
            idx = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        return l2_normalize(vec)
