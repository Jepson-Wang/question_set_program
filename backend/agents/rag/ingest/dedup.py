"""
两级去重，都放在复核之前——重复的题不值得再花一次裁判调用。

1. 精确去重：id（规范化题干的哈希）相同即重复，包括与库中已有的题、与同批次的题。
2. 近似去重：与库中已有题目、或同批次已保留题目的余弦相似度不低于阈值即重复。
   用来兜住「同一道题在两个重叠窗口里被转写得略有不同」和「LLM 批量生成时换汤不换药」。
"""
from typing import Callable, Sequence

from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.embedder import dot
from backend.agents.rag.store.vector_store import VectorStore


def drop_exact_duplicates(items: Sequence[QuestionItem],
                          exists: Callable[[str], bool]) -> tuple[list[QuestionItem], int]:
    """返回 (保留的题目, 重复数)。exists(id) 判断库里是否已有。"""
    seen: set[str] = set()
    kept: list[QuestionItem] = []
    duplicates = 0
    for item in items:
        # 规范化后文本相同 → id 相同，所以比 id 就够了，不用再比题干本身
        if item.id in seen or exists(item.id):
            duplicates += 1
            continue
        seen.add(item.id)
        kept.append(item)
    return kept, duplicates


async def drop_near_duplicates(items: Sequence[QuestionItem], embeddings: Sequence[Sequence[float]],
                               store: VectorStore, threshold: float
                               ) -> tuple[list[QuestionItem], list[list[float]], int]:
    """返回 (保留的题目, 对应的向量, 重复数)。向量须已归一化。"""
    kept: list[QuestionItem] = []
    kept_vectors: list[list[float]] = []
    duplicates = 0
    # strict=True：题目和向量数量对不上是调用方的 bug，必须立刻暴露而不是静默截断
    for item, vector in zip(items, embeddings, strict=True):
        # 先比同批次已经收下的题：它们还没进库，不比的话本批内部的重复会漏网
        if any(dot(vector, other) >= threshold for other in kept_vectors):
            duplicates += 1
            continue
        # 再比库里已有的题：库里的题是上一批入的，同一道题换个说法再来一次就靠这里挡住
        hits = await store.query(vector, top_k=1)
        if hits and hits[0].similarity >= threshold:
            duplicates += 1
            continue
        kept.append(item)
        kept_vectors.append(list(vector))
    return kept, kept_vectors, duplicates
