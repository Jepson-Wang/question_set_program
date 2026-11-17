import pytest

from backend.agents.rag.ingest.dedup import drop_exact_duplicates, drop_near_duplicates
from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.vector_store import InMemoryVectorStore
from backend.tests.agents.rag.helpers import HashingEmbedder

LONG = "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少"


def _q(stem):
    return QuestionItem(stem=stem, answer="略")


def test_exact_duplicates_within_batch_and_against_store():
    existing = _q("解方程 2x+3=7")
    items = [_q("解方程2x+3=7"), _q("分解因式 x²-9"), _q("分解因式 x² - 9")]
    kept, duplicates = drop_exact_duplicates(items, exists=lambda i: i == existing.id)
    assert [k.stem for k in kept] == ["分解因式 x²-9"]
    assert duplicates == 2


async def test_near_duplicates_within_batch():
    embedder = HashingEmbedder()
    items = [_q(LONG), _q(LONG + "？"), _q("计算半径为3的圆的面积")]
    vectors = await embedder.embed([i.stem for i in items])
    kept, kept_vectors, duplicates = await drop_near_duplicates(items, vectors, InMemoryVectorStore(), 0.9)
    assert [k.stem for k in kept] == [LONG, "计算半径为3的圆的面积"]
    assert len(kept_vectors) == 2 and duplicates == 1


async def test_near_duplicates_against_store():
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    [stored_vec] = await embedder.embed([LONG])
    await store.upsert(["q_old"], [stored_vec], [LONG], [{}])

    items = [_q(LONG + "？")]
    vectors = await embedder.embed([items[0].stem])
    kept, _, duplicates = await drop_near_duplicates(items, vectors, store, 0.9)
    assert kept == [] and duplicates == 1


def test_exact_dedup_keeps_texts_that_only_look_similar():
    """精确去重只认规范化后完全相同的文本，"看起来像"是近似去重的活。"""
    items = [_q("解方程2x+3=7"), _q("解方程2x+3=8")]
    kept, duplicates = drop_exact_duplicates(items, exists=lambda i: False)
    assert [k.stem for k in kept] == ["解方程2x+3=7", "解方程2x+3=8"]
    assert duplicates == 0
    assert len({k.id for k in kept}) == 2


def test_exact_dedup_keeps_the_first_occurrence_in_order():
    """同一批次里重复的题只留最早出现的那道，其余计入重复数。"""
    items = [_q("分解因式 x²-9"), _q("其他题"), _q("分解因式 x² - 9")]
    kept, duplicates = drop_exact_duplicates(items, exists=lambda i: False)
    assert [k.stem for k in kept] == ["分解因式 x²-9", "其他题"]
    assert duplicates == 1
    assert kept[0].id == items[2].id


async def test_near_dedup_returns_vectors_aligned_with_kept_items():
    """返回的向量必须和保留下来的题目一一对应，否则上游存库会存错。"""
    embedder = HashingEmbedder()
    items = [_q(LONG), _q(LONG + "？"), _q("计算半径为3的圆的面积")]
    vectors = await embedder.embed([i.stem for i in items])
    kept, kept_vectors, duplicates = await drop_near_duplicates(items, vectors, InMemoryVectorStore(), 0.9)
    assert duplicates == 1
    assert len(kept_vectors) == len(kept)
    expected = await embedder.embed([k.stem for k in kept])
    assert kept_vectors == expected


async def test_near_dedup_rejects_mismatched_lengths():
    """题目和向量数量对不上是调用方的 bug，必须立刻炸掉而不是静默截断。"""
    embedder = HashingEmbedder()
    items = [_q(LONG), _q("计算半径为3的圆的面积")]
    vectors = await embedder.embed([LONG])
    with pytest.raises(ValueError):
        await drop_near_duplicates(items, vectors, InMemoryVectorStore(), 0.9)
