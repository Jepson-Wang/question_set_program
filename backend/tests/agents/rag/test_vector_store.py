import asyncio
import math

import pytest
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.vector_stores import SimpleVectorStore

from backend.agents.rag.store.embedder import LlamaIndexEmbedder, dot, l2_normalize
from backend.agents.rag.store.llama_store import LlamaIndexVectorStore, chroma_score_to_cosine
from backend.agents.rag.store.vector_store import InMemoryVectorStore
from backend.tests.agents.rag.helpers import HashingEmbedder


def test_l2_normalize_gives_unit_length():
    v = l2_normalize([3.0, 4.0])
    assert v == pytest.approx([0.6, 0.8])
    assert math.isclose(dot(v, v), 1.0)


def test_l2_normalize_zero_vector_is_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


# ---------- 向量化 ----------

async def test_embedder_normalizes_model_output():
    class FakeModel:
        async def aget_text_embedding_batch(self, texts):
            return [[3.0, 4.0] for _ in texts]

    vectors = await LlamaIndexEmbedder(model=FakeModel()).embed(["a", "b"])
    assert vectors == [pytest.approx([0.6, 0.8]), pytest.approx([0.6, 0.8])]


async def test_embedder_empty_input_makes_no_call():
    class ExplodingModel:
        async def aget_text_embedding_batch(self, texts):
            raise AssertionError("空输入不应调用接口")

    assert await LlamaIndexEmbedder(model=ExplodingModel()).embed([]) == []


async def test_embedder_sends_one_bounded_batch_at_a_time():
    """25 条文本分成 10、10、5 三批，且同一时刻只有一批在请求：并发量不随文本数增长。"""
    class RecordingModel:
        def __init__(self):
            self.batches, self.in_flight, self.max_in_flight = [], 0, 0

        async def aget_text_embedding_batch(self, texts):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0)
            self.in_flight -= 1
            self.batches.append(len(texts))
            return [[1.0, 0.0] for _ in texts]

    model = RecordingModel()
    vectors = await LlamaIndexEmbedder(model=model, batch_size=10).embed([f"t{i}" for i in range(25)])
    assert len(vectors) == 25
    assert model.batches == [10, 10, 5]
    assert model.max_in_flight == 1


async def test_embedder_accepts_a_real_llamaindex_embedding():
    """真正的 LlamaIndex BaseEmbedding 子类可以直接接入（MockEmbedding 固定返回 [0.5] * 维度）。"""
    [vector] = await LlamaIndexEmbedder(model=MockEmbedding(embed_dim=4)).embed(["任意文本"])
    assert vector == pytest.approx([0.5, 0.5, 0.5, 0.5])


async def test_hashing_embedder_is_a_trustworthy_double():
    """测试替身本身也要可信：几乎相同的文本相似度要高，无关文本要低。"""
    a, b, c = await HashingEmbedder().embed([
        "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少",
        "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少？",
        "计算半径为3的圆的面积",
    ])
    assert dot(a, b) > 0.9
    assert dot(a, c) < 0.5


# ---------- 向量存储：两种实现跑同一套契约测试 ----------
# 其余测试都用内存实现，生产用 LlamaIndex + Chroma；这组测试保证两者行为一致，可以互换。

@pytest.fixture(params=["memory", "llamaindex_chroma"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryVectorStore()
    # 必须显式传 exc_type=ImportError：chromadb「找得到但导入报错」（如编译扩展缺失）时，
    # pytest 9.1 起默认会把跳过改成报错
    pytest.importorskip("chromadb", exc_type=ImportError)
    return LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "contract")


async def test_query_returns_cosine_similarity_in_order(store):
    """mid 与查询向量的余弦是 0.6：不换算的话，LlamaIndex + Chroma 会给出 exp(-0.4) ≈ 0.67。"""
    await store.upsert(
        ["same", "mid", "orth"],
        [[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]],
        ["doc-same", "doc-mid", "doc-orth"],
        [{"k": "same", "n": 1}, {"k": "mid"}, {"k": "orth"}],
    )
    hits = await store.query([1.0, 0.0], top_k=3)
    assert [h.id for h in hits] == ["same", "mid", "orth"]
    assert [h.similarity for h in hits] == pytest.approx([1.0, 0.6, 0.0], abs=1e-3)
    assert hits[0].document == "doc-same" and hits[0].metadata == {"k": "same", "n": 1}
    assert [h.id for h in await store.query([1.0, 0.0], top_k=2)] == ["same", "mid"]
    assert len(await store.query([1.0, 0.0], top_k=10)) == 3


async def test_upsert_overwrites_and_delete_removes(store):
    await store.upsert(["x"], [[1.0, 0.0]], ["old"], [{"v": 1}])
    await store.upsert(["x"], [[0.0, 1.0]], ["new"], [{"v": 2}])
    assert await store.count() == 1
    assert await store.get_all() == [("x", "new", {"v": 2})]
    [hit] = await store.query([0.0, 1.0], top_k=1)
    assert hit.similarity == pytest.approx(1.0, abs=1e-3)  # 向量也跟着更新了
    await store.delete(["x", "never-existed"])
    assert await store.count() == 0
    assert await store.query([1.0, 0.0], top_k=5) == []


async def test_duplicate_ids_in_one_batch_keep_the_last(store):
    await store.upsert(["x", "x"], [[1.0, 0.0], [1.0, 0.0]], ["first", "second"], [{}, {}])
    assert await store.get_all() == [("x", "second", {})]


async def test_empty_operations_are_noops(store):
    await store.upsert([], [], [], [])
    await store.delete([])
    assert await store.query([1.0, 0.0], top_k=3) == []
    assert await store.get_all() == []
    assert await store.count() == 0


# ---------- LlamaIndex 适配层 ----------

def test_chroma_score_conversion():
    for cosine in (1.0, 0.6, 0.0, -0.5):
        assert chroma_score_to_cosine(math.exp(-(1.0 - cosine))) == pytest.approx(cosine)


def test_rejects_backend_that_does_not_store_text():
    """SimpleVectorStore 只存向量、不存原文，查询结果里拿不到题干，必须在构造时就拒绝。"""
    with pytest.raises(ValueError, match="不保存原文"):
        LlamaIndexVectorStore(SimpleVectorStore(), lambda score: score)


async def test_llamaindex_chroma_persists_across_instances(tmp_path):
    pytest.importorskip("chromadb", exc_type=ImportError)
    first = LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "persist")
    await first.upsert(["x"], [[1.0, 0.0]], ["doc"], [{"k": "v"}])
    reopened = LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "persist")
    assert await reopened.get_all() == [("x", "doc", {"k": "v"})]
    assert (tmp_path / "chroma" / "chroma.sqlite3").exists()


def test_from_chroma_rejects_a_collection_that_is_not_cosine(tmp_path):
    """
    集合的距离空间在创建时定型，之后再传 metadata 不会改、也不报错。
    一个用默认 l2 空间建出来的集合会被原样复用，而余弦换算只在 cosine 空间成立：
    实测同一批向量在 l2 空间下换算成 [1.0, 0.2, -1.0]，而不是 [1.0, 0.6, 0.0]，
    而且全程没有任何异常。所以必须在建库时校验，不能等相似度算错了才发现。
    """
    pytest.importorskip("chromadb", exc_type=ImportError)
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    client.get_or_create_collection("legacy")  # 默认 l2 空间

    with pytest.raises(ValueError, match="cosine"):
        LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "legacy")


async def test_cosine_collection_is_accepted(tmp_path):
    """对照组：正常建出来的 cosine 集合要能正常打开，不被上面的校验误伤。"""
    pytest.importorskip("chromadb", exc_type=ImportError)
    store = LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "cosine_ok")
    await store.upsert(["x"], [[1.0, 0.0]], ["doc"], [{"k": "v"}])
    assert await store.count() == 1
