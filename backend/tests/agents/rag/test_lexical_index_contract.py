"""
词法索引的边界与契约测试。

plan 里的 12 条用例（tests/agents/rag/test_lexical_index.py）覆盖主路径；
这里补的是「实现细节一漂移就会出问题」的地方：分词层的空值与词典规则、
快照替换的完整性、BM25 与知识点召回的排序和过滤边界、向量库重建的计数语义。

断言全部从 backend/agents/rag/store/lexical_index.py 的实现推出来：
分词走 lcut_for_search + _is_meaningful，词典只收 len >= 2 的词，
快照是整体替换（一次赋值），召回按 (-分数, id) 排序。
"""
from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.lexical_index import LexicalIndex
from backend.agents.rag.store.vector_store import InMemoryVectorStore


def _q(stem, kps, grade="七年级", difficulty="中等"):
    return QuestionItem(stem=stem, answer="略", knowledge_points=kps, grade=grade, difficulty=difficulty)


# 前两篇结构完全对称（只差一个尾字），用来做同分排序；后三篇互不相关。
# BM25 的 idf 需要语料里有足够多篇文档：只有 2 篇时，只出现在其中 1 篇的词 idf 恰好为 0。
CORPUS = [
    _q("解方程甲", ["知识点甲"]),
    _q("解方程乙", ["知识点乙"]),
    _q("求三角形面积底 3 高 4", ["三角形面积"]),
    _q("求圆的半径已知周长 12", ["圆的周长"]),
    _q("求等差数列通项第一项 2 公差 3", ["等差数列"]),
]


# ---------------------------------------------------------------- 分词层


def test_tokenize_returns_empty_list_for_empty_and_none_text():
    """契约 1：tokenize 对空串和 None 都返回空列表，不能抛异常。

    实现里是 lcut_for_search(text or "")，空值被兜成空串。
    """
    index = LexicalIndex()
    assert index.tokenize("") == []
    assert index.tokenize(None) == []


def test_add_words_ignores_words_shorter_than_two_characters():
    """契约 2：add_words 只收长度 >= 2 的词，单字词直接被忽略。

    实现里是 if len(word) >= 2 才 tokenizer.add_word，所以单字词根本进不了词典。
    """
    index = LexicalIndex()
    index.add_words(["䶮"])
    assert "䶮" not in index._tokenizer.FREQ
    index.add_words(["龗龘"])
    assert "龗龘" in index._tokenizer.FREQ


def test_add_words_tolerates_blank_and_none_entries():
    """契约 2 的延伸：空串、纯空白、None 都不能把 add_words 弄崩。

    实现里先做 (word or "").strip()，再按长度过滤。
    """
    index = LexicalIndex()
    index.add_words(["", "   ", None])
    assert index.tokenize("") == []
    assert "" not in index._tokenizer.FREQ


def test_extra_words_are_tokenized_as_whole_terms():
    """契约 3：构造参数 extra_words 里的词要能作为整词被分出来。

    实现里 __init__ 把 extra_words 交给 add_words，与内置术语走同一条路。
    """
    index = LexicalIndex(extra_words=["火星语"])
    assert "火星语" in index.tokenize("火星语")
    assert "火星语" not in LexicalIndex().tokenize("火星语")  # 默认实例没有这个词


def test_two_indexes_do_not_share_their_dictionaries():
    """契约 4：两个 LexicalIndex 各用自己的 jieba.Tokenizer，A 加的词不能影响 B。

    实现里 self._tokenizer = jieba.Tokenizer()，不是全局 jieba.dict。
    """
    first = LexicalIndex()
    second = LexicalIndex()
    first.add_words(["火星语"])
    assert "火星语" in first.tokenize("火星语")
    assert "火星语" not in second.tokenize("火星语")


# ---------------------------------------------------------------- 重建层


def test_rebuild_sets_size_and_lookups_of_unknown_id_are_empty():
    """契约 5：rebuild 之后 size 等于题目条数；查不到的 id 一律没有。"""
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert index.size == len(CORPUS)
    assert index.contains("q_does_not_exist") is False
    assert index.get("q_does_not_exist") is None


def test_old_snapshot_keeps_its_own_items_and_bm25_after_rebuild():
    """契约 6：快照是整体替换，旧快照的 items 与 bm25 必须原样留在旧语料上。

    实现里是一次赋值 self._snapshot = _Snapshot(...)：新快照是新对象，
    旧对象手里的 items 字典和 BM25 实例都不该被就地改动，
    否则正在用旧快照的检索会读到半新半旧的数据。
    """
    index = LexicalIndex()
    index.rebuild(CORPUS[:2])
    old = index._snapshot
    old_items = dict(old.items)
    old_bm25 = old.bm25

    index.rebuild(CORPUS)

    assert index._snapshot is not old
    assert old.items == old_items
    assert old.bm25 is old_bm25
    assert len(old.items) == 2
    assert CORPUS[2].id not in old.items
    # 旧 BM25 仍然只描述旧语料的两篇，不是重建后的五篇
    assert len(old.bm25.get_scores(index.tokenize("方程"))) == 2
    assert len(index._snapshot.items) == len(CORPUS)


def test_rebuild_keeps_the_first_of_two_items_with_the_same_id():
    """契约 7：同一 id 的重复题目只保留一条，而且是先到先得。

    实现里是 by_id.setdefault(item.id, item)，后来者不覆盖先来者。
    """
    first = QuestionItem(stem="重复题目", answer="答案甲", knowledge_points=["知识点甲"], difficulty="简单")
    second = QuestionItem(stem="重复题目", answer="答案乙", knowledge_points=["知识点乙"], difficulty="困难")
    assert first.id == second.id

    index = LexicalIndex()
    index.rebuild([first, second])

    assert index.size == 1
    kept = index.get(first.id)
    assert kept.answer == "答案甲"
    assert kept.difficulty == "简单"
    # 被丢弃的那条也不进知识点倒排表
    assert index.search_knowledge_points(["知识点乙"], top_k=5) == []


def test_knowledge_point_dictionary_accumulates_across_rebuilds():
    """契约 8：知识点入词典是累积的——新一批的知识点此后也能作为整词分出来。

    实现里 rebuild 每次都调 add_words，词典只增不减；快照被换掉，
    但词典里的旧词仍然留着。
    """
    index = LexicalIndex()
    first = _q("第一批题目", ["知识点甲"])
    second = _q("第二批题目", ["知识点乙"])

    index.rebuild([first])
    index.rebuild([second])

    assert "知识点乙" in index.tokenize("知识点乙")
    assert "知识点甲" in index.tokenize("知识点甲")
    # 索引内容只反映最后一批
    assert index.size == 1
    assert index.contains(first.id) is False
    assert index.contains(second.id) is True


# ---------------------------------------------------------------- BM25 召回层


def test_search_bm25_returns_nothing_for_empty_text():
    """契约 9：空文本没有 token，直接返回空列表。

    实现里 tokens 为空就 return []。
    """
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert index.search_bm25("", top_k=5) == []


def test_search_bm25_returns_nothing_when_no_term_matches():
    """契约 10：完全无关的文本一个词都命中不了，返回空列表。

    实现里只保留 s > 0 的文档，命中不了就一个都不剩。
    """
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert index.search_bm25("紫罗兰蝴蝶量子纠缠", top_k=5) == []


def test_search_bm25_respects_top_k():
    """契约 11：返回条数受 top_k 限制，实现里是 ranked[:top_k]。"""
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert len(index.search_bm25("方程", top_k=5)) == 2  # 语料里只有两篇含「方程」
    assert len(index.search_bm25("方程", top_k=1)) == 1
    assert index.search_bm25("方程", top_k=0) == []


def test_search_bm25_orders_by_score_desc_then_id_asc():
    """契约 12：先按分数降序，分数相同时按 id 升序。

    实现里是 ranked.sort(key=lambda p: (-p[1], p[0]))。
    前两篇语料 token 结构相同，BM25 分数必然相同，正好卡住这条排序规则。
    """
    index = LexicalIndex()
    # 故意让两篇同分题目的插入顺序与 id 升序相反，否则测不出「同分按 id 排」
    tied = sorted([CORPUS[0], CORPUS[1]], key=lambda item: item.id, reverse=True)
    index.rebuild([*tied, *CORPUS[2:]])

    results = index.search_bm25("方程", top_k=5)

    assert len(results) == 2
    assert results[0][1] == results[1][1]
    ids = [item_id for item_id, _ in results]
    assert ids == sorted(ids)
    assert ids != [item.id for item in tied]  # 不是按语料顺序返回的


# ---------------------------------------------------------------- 知识点召回层


def test_search_knowledge_points_returns_nothing_for_unknown_knowledge_point():
    """契约 13：完全未知的知识点在倒排表里查不到，返回空列表。

    实现里是 snap.kp_index.get(kp, ())，查不到就不产生任何候选。
    """
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert index.search_knowledge_points(["不存在的知识点"], top_k=5) == []


def test_search_knowledge_points_respects_top_k():
    """契约 14：知识点召回同样受 top_k 限制，实现里是 results[:top_k]。"""
    items = [_q(f"第{n}题共享同一个知识点", ["共享知识点"]) for n in range(1, 4)]
    index = LexicalIndex()
    index.rebuild(items)

    assert len(index.search_knowledge_points(["共享知识点"], top_k=3)) == 3
    assert len(index.search_knowledge_points(["共享知识点"], top_k=2)) == 2
    assert index.search_knowledge_points(["共享知识点"], top_k=0) == []


def test_items_without_a_known_grade_survive_the_grade_filter():
    """契约 15：年级认不出来的题不能被年级过滤掉。

    实现里是 rank is not None and rank > max_grade_rank，
    grade=None 或 grade='未知' 时 grade_rank 返回 None，条件不成立，题目保留。
    """
    no_grade = _q("没有年级的题目", ["混合知识点"], grade=None)
    weird_grade = _q("年级写法的题目", ["混合知识点"], grade="未知")
    high_grade = _q("九年级的题目", ["混合知识点"], grade="九年级")

    index = LexicalIndex()
    index.rebuild([no_grade, weird_grade, high_grade])

    ids = [item_id for item_id, _ in index.search_knowledge_points(
        ["混合知识点"], top_k=5, max_grade_rank=0)]
    assert no_grade.id in ids
    assert weird_grade.id in ids
    assert high_grade.id not in ids


def test_max_grade_rank_none_disables_the_grade_filter():
    """契约 16：max_grade_rank=None 时完全不做年级过滤。"""
    low = _q("四年级的题目", ["混合知识点"], grade="四年级")
    high = _q("九年级的题目", ["混合知识点"], grade="九年级")

    index = LexicalIndex()
    index.rebuild([low, high])

    ids = [item_id for item_id, _ in index.search_knowledge_points(["混合知识点"], top_k=5)]
    assert set(ids) == {low.id, high.id}


def test_difficulty_none_adds_no_bonus():
    """契约 17：difficulty=None 时不加那 0.5 分，只有难度一致才加分。

    实现里 bonus = 0.5 if difficulty and item.difficulty == difficulty else 0.0。
    """
    hard = _q("一道难题", ["共享知识点"], difficulty="困难")
    easy = _q("一道简单题", ["共享知识点"], difficulty="简单")

    index = LexicalIndex()
    index.rebuild([hard, easy])

    scores = dict(index.search_knowledge_points(["共享知识点"], top_k=5))
    assert scores[hard.id] == 1.0
    assert scores[easy.id] == 1.0

    scores = dict(index.search_knowledge_points(["共享知识点"], top_k=5, difficulty="困难"))
    assert scores[hard.id] == 1.5
    assert scores[easy.id] == 1.0


# ---------------------------------------------------------------- 从向量库重建


async def test_rebuild_from_store_restores_the_same_index_as_rebuild():
    """契约 18：全部记录都能解析时，重建结果与直接 rebuild(items) 完全一致。

    实现里是 QuestionItem.from_record(document, metadata) 还原，再交给同一个 rebuild。
    """
    store = InMemoryVectorStore()
    await store.upsert(
        [item.id for item in CORPUS],
        [[1.0, 0.0] for _ in CORPUS],
        [item.stem for item in CORPUS],
        [item.to_metadata() for item in CORPUS],
    )

    index = LexicalIndex()
    assert await index.rebuild_from_store(store) == len(CORPUS)
    assert index.size == len(CORPUS)

    direct = LexicalIndex()
    direct.rebuild(CORPUS)
    assert index._snapshot.ids == direct._snapshot.ids
    assert index._snapshot.items == direct._snapshot.items
    assert index._snapshot.kp_index == direct._snapshot.kp_index
    assert index.search_bm25("方程", top_k=5) == direct.search_bm25("方程", top_k=5)


async def test_rebuild_from_store_skips_bad_records_and_counts_only_parsed_ones():
    """契约 19：解析不了的记录被跳过，其余照常入库；返回值是成功解析的条数。

    实现里只把解析成功的追加进 items，最后 return len(items)，不是语料总条数。
    """
    good_a = CORPUS[0]
    good_b = CORPUS[2]
    store = InMemoryVectorStore()
    await store.upsert(
        [good_a.id, "bad_no_answer", good_b.id, "bad_no_stem"],
        [[1.0] for _ in range(4)],
        [good_a.stem, "答案为空的记录", good_b.stem, ""],
        [good_a.to_metadata(), {"answer": ""}, good_b.to_metadata(), {"answer": "略"}],
    )
    assert await store.count() == 4

    index = LexicalIndex()
    assert await index.rebuild_from_store(store) == 2  # 不是语料总条数 4
    assert index.size == 2
    assert index.contains(good_a.id) is True
    assert index.contains(good_b.id) is True
    assert index.get(good_a.id).stem == good_a.stem


# ------------------------------------------------- BM25 在极小语料上的边界


def test_bm25_returns_nothing_when_the_corpus_has_only_two_documents():
    """
    特征化测试：把 BM25 在极小语料上「静默返回空」这个限制钉住。

    rank_bm25 的 idf = log(N-f+0.5) - log(f+0.5)：语料只有 2 篇时，
    只出现在 1 篇里的词 idf 恰好是 0，出现在全部 2 篇里的词 idf 是负数，
    于是所有文档得分都 <= 0，而实现只保留 s > 0 的——一条都返回不了。

    这不是 bug，是 BM25 在极小语料上本来就没有区分度。真实知识库只导入
    1~2 道题时，关键词这一路会没有结果，但向量召回与知识点召回不受影响。
    将来若要改掉这个行为（例如换打分方式），这条测试会失败——那时它是提醒，
    不是障碍。
    """
    two = [_q("三角形面积", ["知识点甲"]), _q("鸡兔同笼", ["知识点乙"])]
    index = LexicalIndex()
    index.rebuild(two)
    assert index.search_bm25("三角形", top_k=5) == []


def test_bm25_starts_working_from_three_documents():
    """对照组：3 篇语料时，只出现在 1 篇里的词 idf 变成 log(2.5)-log(1.5) ≈ 0.51 > 0，能召回。"""
    three = [
        _q("三角形面积", ["知识点甲"]),
        _q("鸡兔同笼", ["知识点乙"]),
        _q("长方形周长", ["知识点丙"]),
    ]
    index = LexicalIndex()
    index.rebuild(three)
    results = index.search_bm25("三角形", top_k=5)
    assert results and results[0][0] == three[0].id
    assert results[0][1] > 0


def test_bm25_ignores_terms_that_appear_in_more_than_half_the_corpus():
    """
    特征化测试：出现在半数以上文档里的词对所有候选都是负贡献，同样一条都留不下。

    这是 BM25 的固有性质：词越常见区分度越低，负 idf 会把它从所有候选里扣掉。
    """
    four = [
        _q("三角形面积", ["知识点甲"]),
        _q("三角形周长", ["知识点丁"]),   # 「三角形」出现在 2 篇里
        _q("鸡兔同笼", ["知识点乙"]),
        _q("长方形周长", ["知识点丙"]),
    ]
    index = LexicalIndex()
    index.rebuild(four)
    assert index.search_bm25("三角形", top_k=5) == []
