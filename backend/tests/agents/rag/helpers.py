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
