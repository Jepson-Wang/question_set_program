"""
向量存储的生产实现：把 LlamaIndex 的向量库接口（BasePydanticVectorStore）适配成 VectorStore 协议，
后端用 Chroma。以后换成 LlamaIndex 支持的其他向量库，只需照着 from_chroma 另写一个工厂。

LlamaIndex 与 Chroma 有几处行为和直觉不一样，都已用真实的 Chroma 验证过（Task 0 的探针会再确认一次）：

1. 查询分数不是余弦相似度。LlamaIndex 的 Chroma 集成返回 exp(-距离)，cosine 空间里距离 = 1 - 余弦，
   所以余弦 = 1 + ln(分数)。去重、防泄漏、教材阈值都按余弦定义，必须换算回来
2. add 不是 upsert。id 已存在时 Chroma 静默忽略这次写入、保留旧内容，所以 upsert = 先删后加；
   同一批里 id 重复会报 DuplicateIDError，先在批内去重（后出现的覆盖先出现的，与内存实现一致）
3. delete_nodes([]) 会报错，空列表要提前返回
4. async_add、aquery 等异步方法只是直接调用同步方法，照样阻塞事件循环；
   所以一律调用同步方法，投递到向量库专属线程池
5. 不经过 VectorStoreIndex，直接操作向量库：向量由 Embedder 事先算好（去重要先拿到向量，
   一次查询的向量要在题库和教材库之间复用），这样不会重复向量化，也不会用到 LlamaIndex 的全局 Settings
   （它默认的 embedding 是 OpenAI）

另外三处实测发现、但 plan 没有覆盖的行为：

6. 集合的距离空间在创建时定型，之后 get_or_create_collection 传进去的 metadata 对已存在的集合
   完全无效，也不报错。若集合是用默认的 l2 空间建的，1+ln(分数) 换算出来的余弦是垃圾值
   （实测同一批向量换算成 [1.0, 0.2, -1.0]，而不是 [1.0, 0.6, 0.0]），而且全程没有任何异常。
   所以 from_chroma 必须显式校验空间，不能等相似度算错了才发现
7. 删不存在的 id 是静默 no-op，不能用 delete 的成败判断 id 是否存在
8. metadata 必须是扁平的 str/int/float/None，嵌套 dict 或 list 会抛 ValueError；
   往返之后节点的 embedding 不保留（embedding 不进 metadata，取回时要自备向量）
"""
import asyncio
import math
from functools import partial
from pathlib import Path
from typing import Callable

from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import BasePydanticVectorStore, VectorStoreQuery

from backend.agents.rag.store.vector_store import VectorHit
from backend.core.executors import get_vector_executor


def chroma_score_to_cosine(score: float) -> float:
    """LlamaIndex 的 Chroma 集成返回 exp(-cosine 距离)，换算回余弦相似度。"""
    return 1.0 + math.log(score)


def _collection_space(collection) -> str | None:
    """
    读出集合实际使用的距离空间。

    优先看 configuration（1.5.5 里它是 hnsw 参数的真实来源，不管空间当初是用
    metadata= 还是 configuration= 建的都能读到），再退回 metadata。
    """
    configuration = getattr(collection, "configuration", None) or {}
    hnsw = configuration.get("hnsw") or {}
    space = hnsw.get("space")
    if space:
        return space
    return (collection.metadata or {}).get("hnsw:space")


class LlamaIndexVectorStore:
    def __init__(self, backend: BasePydanticVectorStore, score_to_similarity: Callable[[float], float]):
        if not backend.stores_text:
            # 例如 LlamaIndex 自带的 SimpleVectorStore：原文要另存在 docstore 里，查询结果拿不到文本
            raise ValueError(f"{type(backend).__name__} 不保存原文，不能作为 RAG 的向量库")
        self._backend = backend
        self._to_similarity = score_to_similarity

    @classmethod
    def from_chroma(cls, persist_dir: Path, collection_name: str) -> "LlamaIndexVectorStore":
        # 延迟导入：chromadb 导入失败时，只有真正要用它的地方报错
        import chromadb
        from llama_index.vector_stores.chroma import ChromaVectorStore

        persist_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(persist_dir))
        collection = client.get_or_create_collection(collection_name, metadata={"hnsw:space": "cosine"})

        # 集合的距离空间在创建时定型。对已存在的集合再传 metadata 完全无效、也不报错，
        # 所以一个用 l2 建出来的集合会被原样复用——而余弦换算只在 cosine 空间成立。
        # 不校验的话，相似度会静默算错，且没有任何异常提示。
        space = _collection_space(collection)
        if space != "cosine":
            raise ValueError(
                f"集合 {collection_name} 的距离空间是 {space!r}，不是 cosine。"
                f"余弦换算只在 cosine 空间成立，用错空间会让相似度静默失真。"
                f"请删掉该集合重建。"
            )

        return cls(ChromaVectorStore(chroma_collection=collection), chroma_score_to_cosine)

    async def _run(self, fn, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(get_vector_executor(), partial(fn, *args, **kwargs))

    async def upsert(self, ids, embeddings, documents, metadatas) -> None:
        latest: dict[str, TextNode] = {}
        for row_id, vec, doc, meta in zip(ids, embeddings, documents, metadatas, strict=True):
            latest[row_id] = TextNode(id_=row_id, text=doc, embedding=list(vec), metadata=dict(meta))
        if latest:
            await self._run(self._replace, list(latest.values()))

    def _replace(self, nodes: list[TextNode]) -> None:
        # 查询恰好落在两步之间时，会暂时查不到这几条；入库是低频操作，可以接受
        self._backend.delete_nodes([node.node_id for node in nodes])
        self._backend.add(nodes)

    async def query(self, embedding, top_k) -> list[VectorHit]:
        result = await self._run(
            self._backend.query,
            VectorStoreQuery(query_embedding=list(embedding), similarity_top_k=top_k),
        )
        return [
            VectorHit(node.node_id, node.get_content(), dict(node.metadata), self._to_similarity(score))
            for node, score in zip(result.nodes or [], result.similarities or [])
        ]

    async def get_all(self) -> list[tuple[str, str, dict]]:
        nodes = await self._run(self._backend.get_nodes, node_ids=None)
        return [(node.node_id, node.get_content(), dict(node.metadata)) for node in nodes]

    async def count(self) -> int:
        # LlamaIndex 没有通用的计数接口；这个方法只在测试与统计里用，全量读取可以接受
        return len(await self.get_all())

    async def delete(self, ids) -> None:
        ids = list(ids)
        if ids:
            await self._run(self._backend.delete_nodes, ids)
