"""RAG 测试用的替身与工具。全部离线、结果确定。"""
import hashlib
from types import SimpleNamespace

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


class ScriptedLLM:
    """
    按调用顺序依次返回预设内容，模拟 LangChain 聊天模型的 ainvoke。
    预设内容是 Exception 实例时抛出它；calls 记录每次收到的消息列表。
    """

    def __init__(self, *outputs):
        self._outputs = list(outputs)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._outputs:
            raise AssertionError("ScriptedLLM 的预设输出已经用完")
        output = self._outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(content=output)


class FunctionLLM:
    """根据收到的消息动态决定返回内容：fn(messages) -> str。"""

    def __init__(self, fn):
        self._fn = fn
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self._fn(messages))


def write_text_pdf(path, text: str) -> None:
    """
    手写一个最小的合法 PDF：单页、Helvetica 字体、一行文字。
    只支持 ASCII 文本，足够验证加载器；不引入 reportlab 之类的依赖。
    """
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + " /Root 1 0 R >>\n".encode()
    out += b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    path.write_bytes(bytes(out))


def write_docx(path, paragraphs, table_rows=()) -> None:
    """段落在前、表格在后；table_rows 是二维列表。"""
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, value in enumerate(row):
                table.cell(r, c).text = value
    document.save(str(path))


# ---------- 入库服务的替身 ----------

# SimpleNamespace 已在文件顶部导入，这里直接用，避免重复导入被 lint 判成重定义
import asyncio

from backend.agents.rag.models import JudgeResult


class FakeJudge:
    """reject_stems 里的题判为不通过，其余通过；同时记录调用次数与最大并发数。"""

    def __init__(self, reject_stems=(), delay: float = 0.0):
        self.reject_stems = set(reject_stems)
        self.delay = delay
        self.calls = 0
        self.max_concurrency = 0
        self._running = 0

    async def judge(self, item):
        self.calls += 1
        self._running += 1
        self.max_concurrency = max(self.max_concurrency, self._running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            passed = item.stem not in self.reject_stems
            reasons = [] if passed else ["答案与独立解答不一致"]
            return JudgeResult(passed, 0.9 if passed else 0.3, reasons, judge_model="fake-judge")
        finally:
            self._running -= 1


class ListQuarantine:
    """隔离区替身：把 (题目, 复核结果) 记在列表里；fail=True 时模拟数据库故障。"""

    def __init__(self, fail: bool = False):
        self.entries = []
        self.fail = fail

    async def __call__(self, item, result):
        if self.fail:
            raise RuntimeError("数据库连不上")
        self.entries.append((item, result))


def make_ingest_service(settings, *, structurer_llm=None, generator_llm=None, judge=None, quarantine=None):
    """用全套离线替身组装入库服务，返回 (service, parts)。"""
    from backend.agents.rag.ingest.generator import SeedGenerator
    from backend.agents.rag.ingest.service import IngestService
    from backend.agents.rag.ingest.structurer import Structurer
    from backend.agents.rag.store.lexical_index import LexicalIndex
    from backend.agents.rag.store.vector_store import InMemoryVectorStore

    parts = SimpleNamespace(
        embedder=HashingEmbedder(),
        questions=InMemoryVectorStore(),
        knowledge=InMemoryVectorStore(),
        index=LexicalIndex(),
        judge=judge or FakeJudge(),
        quarantine=quarantine or ListQuarantine(),
    )
    service = IngestService(
        settings=settings,
        embedder=parts.embedder,
        question_store=parts.questions,
        knowledge_store=parts.knowledge,
        lexical_index=parts.index,
        structurer=Structurer(structurer_llm) if structurer_llm else None,
        generator=SeedGenerator(generator_llm) if generator_llm else None,
        judge=parts.judge,
        quarantine=parts.quarantine,
    )
    return service, parts


# ---------- 运行时的替身 ----------


def make_test_runtime(settings, *, llm=None, judge=None, judge_error=None, reranker=None):
    """
    用离线替身组装 RagRuntime。llm 同时充当结构化与生成用的模型；
    judge_error 不为空时，构建裁判会抛出它（模拟裁判配置错误）。
    """
    from backend.agents.rag.runtime import RagRuntime
    from backend.agents.rag.store.lexical_index import LexicalIndex
    from backend.agents.rag.store.vector_store import InMemoryVectorStore

    def judge_factory():
        if judge_error is not None:
            raise judge_error
        return judge or FakeJudge()

    return RagRuntime(
        settings,
        embedder=HashingEmbedder(),
        question_store=InMemoryVectorStore(),
        knowledge_store=InMemoryVectorStore(),
        lexical_index=LexicalIndex(),
        llm_factory=lambda: llm,
        judge_factory=judge_factory,
        reranker=reranker,
    )
