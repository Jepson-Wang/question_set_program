from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.lexical_index import LexicalIndex
from backend.agents.rag.store.vector_store import InMemoryVectorStore


def _q(stem, kps, grade="七年级", difficulty="中等"):
    return QuestionItem(stem=stem, answer="略", knowledge_points=kps, grade=grade, difficulty=difficulty)


# BM25 的 idf 需要语料里有足够多篇文档：只有 2 篇时，只出现在其中 1 篇的词 idf 恰好为 0
CORPUS = [
    _q("解一元二次方程 x²-5x+6=0", ["一元二次方程"], grade="九年级"),
    _q("解方程 2x+3=7", ["一元一次方程"]),
    _q("因式分解：x²-9", ["因式分解"], grade="八年级"),
    _q("鸡兔同笼，头 35 个，脚 94 只，鸡兔各几只", ["鸡兔同笼问题"], grade="四年级", difficulty="困难"),
    _q("长方形长 5 厘米、宽 3 厘米，求周长", ["长方形周长"], grade="三年级", difficulty="简单"),
]


def test_whole_terms_and_sub_words_are_both_produced():
    tokens = LexicalIndex().tokenize("解一元二次方程x²-5x+6=0")
    assert "一元二次方程" in tokens  # 整词：标准术语精确匹配
    assert "方程" in tokens          # 子词：不同说法之间也能召回


def test_punctuation_and_symbols_are_dropped():
    tokens = LexicalIndex().tokenize("解方程：2x+3=7。")
    assert not {"：", "+", "=", "。"} & set(tokens)


def test_knowledge_points_join_the_dictionary_on_rebuild():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert "鸡兔同笼问题" in index.tokenize("鸡兔同笼问题怎么列式")


def test_bm25_matches_a_different_wording_of_the_same_term():
    """「分解因式」与「因式分解」：普通分词下两者没有任何共同 token，这里必须能召回。"""
    index = LexicalIndex()
    index.rebuild(CORPUS)
    results = index.search_bm25("分解因式 a²-16", top_k=3)
    assert results and results[0][0] == CORPUS[2].id


def test_bm25_ranks_the_matching_word_problem_first():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    results = index.search_bm25("鸡兔同笼问题：头 20 个、脚 54 只", top_k=3)
    assert results and results[0][0] == CORPUS[3].id


def test_empty_index_returns_nothing():
    index = LexicalIndex()
    assert index.search_bm25("方程", 5) == []
    assert index.search_knowledge_points(["方程"], 5) == []


def test_knowledge_point_search_orders_by_overlap_then_difficulty():
    a = _q("题A", ["一元一次方程", "移项"])
    b = _q("题B", ["一元一次方程"], difficulty="困难")
    c = _q("题C", ["一元一次方程"])
    index = LexicalIndex()
    index.rebuild([a, b, c])
    ids = [i for i, _ in index.search_knowledge_points(["一元一次方程", "移项"], top_k=3, difficulty="困难")]
    assert ids == [a.id, b.id, c.id]  # A 重合 2 个知识点；B 重合 1 个但难度一致加分


def test_knowledge_point_search_filters_higher_grades():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    ids = [i for i, _ in index.search_knowledge_points(["一元二次方程", "一元一次方程"], top_k=5, max_grade_rank=6)]
    assert CORPUS[1].id in ids
    assert CORPUS[0].id not in ids  # 九年级的题不推给七年级学生


def test_rebuild_swaps_snapshot_atomically():
    index = LexicalIndex()
    index.rebuild(CORPUS[:2])
    old = index._snapshot
    index.rebuild(CORPUS)
    assert len(old.ids) == 2, "旧快照不能被原地修改：正在用它的检索必须看到完整一致的数据"
    assert index.size == len(CORPUS)


def test_duplicate_items_are_kept_once():
    index = LexicalIndex()
    index.rebuild([CORPUS[0], CORPUS[0]])
    assert index.size == 1


def test_get_and_contains():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert index.contains(CORPUS[2].id)
    assert index.get(CORPUS[2].id).stem == CORPUS[2].stem
    assert index.get("q_missing") is None


async def test_rebuild_from_store_skips_bad_records():
    good = CORPUS[1]
    store = InMemoryVectorStore()
    await store.upsert([good.id, "bad"], [[1.0], [1.0]], [good.stem, "坏记录"],
                       [good.to_metadata(), {"answer": ""}])
    index = LexicalIndex()
    assert await index.rebuild_from_store(store) == 1
    assert index.contains(good.id)
