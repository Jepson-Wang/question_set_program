"""
词法索引：BM25 关键词召回 + 知识点倒排表 + 题目目录（id -> QuestionItem）。

- rank_bm25 不支持增量添加，只能整体重建。rebuild() 先在旁边建好一份完整快照，
  再用一次赋值替换引用；每个检索方法开头只读一次 self._snapshot，
  拿到的永远是完整一致的一份，不会看到只建了一半的索引。
- 用独立的 jieba.Tokenizer 实例而不是全局 jieba：词典改动不会影响进程里
  其他使用 jieba 的代码，测试之间也不会互相污染。
- 索引与查询都用 jieba 的搜索引擎模式（cut_for_search），它同时产出整词与子词：
  整词让「一元二次方程」这类标准术语精确匹配、得分更高；子词保证「分解因式」与
  「因式分解」、「鸡兔同笼问题」与「鸡兔同笼」这类不同说法仍能互相召回。
  用普通 cut 时，后者两两之间一个共同 token 都没有。
- 题目的知识点名称会被加进分词词典，让它们作为整词出现。
- 知识点倒排放在这里而不是依赖 Chroma 的 metadata 过滤：Chroma 的 metadata
  只接受标量，没法可靠地做「列表里包含某一项」的查询。

实测（jieba 0.42.1 / rank_bm25 0.2.2）确认的四处行为，计划里没写：

6. **语料太小时 BM25 静默返回空**。rank_bm25 的 idf = log(N-f+0.5) - log(f+0.5)：
   语料只有 2 篇时，只出现在 1 篇里的词 idf 恰好是 0，出现在全部篇里的词 idf 是负数，
   于是所有文档得分都 <= 0，而下面只保留 s > 0 的——一条都返回不了。3 篇时只出现
   1 篇的词 idf 变成 log(2.5)-log(1.5) ≈ 0.51 > 0，才有区分度。同理，一个词若出现在
   半数以上文档里，它对所有候选都是负贡献，照样一条都不剩。所以「关键词这一路在
   小题库里没结果」是 BM25 的固有性质，不是 bug；真要修得换打分方式，不在这里。
7. **add_word 会覆盖基础词典的词频，且每调一次 +1**（因式分解 7→8→9→10）。
   rebuild() 每次重建都把所有知识点 add_word 一遍，所以分词器状态随重建次数缓慢漂移。
   效果上无害（词频越高越倾向整词），但「重建是幂等的」这个直觉不成立。
8. **噪声 token 会进 BM25 语料**：`_is_meaningful` 只丢掉全是标点(P*)/符号(S*)的 token，
   而 `²` 是 Unicode 的 No（数字）类，会被保留。连同 `5x`、`16` 这类一起进语料后，
   纯数学题之间会互相「沾分」（实测一道一元二次方程仅凭 `²` 就拿到 0.29 分）。
   若日后评测发现召回噪声大，这里是首要嫌疑点。
9. **jieba.setLogLevel 是进程级全局副作用**：它改的是 jieba.default_logger 的等级，
   进程里其它用全局 jieba 的代码也会跟着变安静。放在模块级可以接受，但要知道它不是实例级的。
   （默认 DEBUG 级别下每 new 一个 Tokenizer 都会刷 4 行初始化日志。）
"""
import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

import jieba
from pydantic import ValidationError
from rank_bm25 import BM25Okapi

from backend.agents.rag.models import QuestionItem, grade_rank
from backend.core.executors import get_vector_executor
from backend.middleware.logging import get_logger

jieba.setLogLevel(logging.WARNING)
logger = get_logger(__name__)

BUILTIN_MATH_TERMS = (
    "一元一次方程", "一元二次方程", "二元一次方程组", "分式方程", "不等式组",
    "因式分解", "整式运算", "有理数运算", "平方根", "立方根", "勾股定理",
    "全等三角形", "相似三角形", "平行四边形", "一次函数", "二次函数",
    "反比例函数", "概率统计", "行程应用题", "工程应用题",
)


@dataclass(frozen=True)
class _Snapshot:
    ids: tuple[str, ...] = ()
    bm25: BM25Okapi | None = None
    items: dict[str, QuestionItem] = field(default_factory=dict)
    kp_index: dict[str, frozenset[str]] = field(default_factory=dict)


def _is_meaningful(token: str) -> bool:
    """去掉只由空白、标点（P*）或符号（S*，如 + = ×）组成的 token：几乎每道题都有，对检索没有区分度。"""
    token = token.strip()
    if not token:
        return False
    return not all(unicodedata.category(ch).startswith(("P", "S")) for ch in token)


class LexicalIndex:
    def __init__(self, extra_words: Iterable[str] = ()):
        self._tokenizer = jieba.Tokenizer()
        self.add_words(BUILTIN_MATH_TERMS)
        self.add_words(extra_words)
        self._snapshot = _Snapshot()

    def add_words(self, words: Iterable[str]) -> None:
        for word in words:
            word = (word or "").strip()
            if len(word) >= 2:
                self._tokenizer.add_word(word)

    def tokenize(self, text: str) -> list[str]:
        return [t.strip() for t in self._tokenizer.lcut_for_search(text or "") if _is_meaningful(t)]

    @property
    def size(self) -> int:
        return len(self._snapshot.ids)

    def contains(self, item_id: str) -> bool:
        return item_id in self._snapshot.items

    def get(self, item_id: str) -> QuestionItem | None:
        return self._snapshot.items.get(item_id)

    def rebuild(self, items: Iterable[QuestionItem]) -> None:
        by_id: dict[str, QuestionItem] = {}
        for item in items:
            by_id.setdefault(item.id, item)
        self.add_words(kp for item in by_id.values() for kp in item.knowledge_points)

        ids = tuple(by_id)
        corpus = [self.tokenize(by_id[i].stem) for i in ids]
        # 空语料或全部分词为空时 BM25Okapi 会除零，直接不建
        bm25 = BM25Okapi(corpus) if any(corpus) else None

        kp_index: dict[str, set[str]] = {}
        for item_id in ids:
            for kp in by_id[item_id].knowledge_points:
                kp_index.setdefault(kp, set()).add(item_id)

        # 一次赋值完成替换；正在检索的调用手里仍是旧快照，互不影响
        self._snapshot = _Snapshot(
            ids=ids,
            bm25=bm25,
            items=by_id,
            kp_index={kp: frozenset(s) for kp, s in kp_index.items()},
        )

    async def rebuild_from_store(self, store) -> int:
        """从向量库全量读出题目重建索引，返回题目数。无法解析的记录跳过并记日志。"""
        items: list[QuestionItem] = []
        for record_id, document, metadata in await store.get_all():
            try:
                items.append(QuestionItem.from_record(document, metadata))
            except ValidationError as e:
                logger.warning("跳过无法解析的题目记录 %s：%s", record_id, e)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(get_vector_executor(), self.rebuild, items)
        return len(items)

    def search_bm25(self, text: str, top_k: int) -> list[tuple[str, float]]:
        snap = self._snapshot
        if snap.bm25 is None:
            return []
        tokens = self.tokenize(text)
        if not tokens:
            return []
        scores = snap.bm25.get_scores(tokens)
        ranked = [(snap.ids[i], float(s)) for i, s in enumerate(scores) if s > 0]
        ranked.sort(key=lambda p: (-p[1], p[0]))
        return ranked[:top_k]

    def search_knowledge_points(
        self,
        knowledge_points: Iterable[str],
        top_k: int,
        max_grade_rank: int | None = None,
        difficulty: str | None = None,
    ) -> list[tuple[str, float]]:
        """按与查询知识点的重合数打分，难度一致加 0.5；已知年级时排除更高年级的题。"""
        snap = self._snapshot
        overlap: dict[str, int] = {}
        for kp in knowledge_points:
            for item_id in snap.kp_index.get(kp, ()):
                overlap[item_id] = overlap.get(item_id, 0) + 1

        results: list[tuple[str, float]] = []
        for item_id, count in overlap.items():
            item = snap.items[item_id]
            rank = grade_rank(item.grade)
            if max_grade_rank is not None and rank is not None and rank > max_grade_rank:
                continue
            bonus = 0.5 if difficulty and item.difficulty == difficulty else 0.0
            results.append((item_id, count + bonus))
        results.sort(key=lambda p: (-p[1], p[0]))
        return results[:top_k]
