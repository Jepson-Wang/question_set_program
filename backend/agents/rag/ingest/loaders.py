"""
文档加载：只负责把文件变成「逐页文本」，不做任何理解。

- PDF：pypdf 按页提取文字层
- Word：python-docx 没有「页」的概念。按文档顺序读取段落与表格，
  累积约 1500 字切成一「页」，供后面的滑动窗口使用
- 没有文字层的 PDF（扫描件）直接拒收：这次不做 OCR，
  与其悄悄入库一堆空内容，不如明确报错
"""
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = (".pdf", ".docx")
DOCX_PAGE_CHARS = 1500
MIN_CHARS_PER_PDF_PAGE = 20


class UnsupportedDocumentError(ValueError):
    pass


@dataclass
class PageText:
    number: int  # 从 1 开始
    text: str


def load_document(path: Path) -> list[PageText]:
    # 入口允许传字符串路径：统一转成 Path，后面就不用到处判断类型
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = _load_pdf(path)
    elif suffix == ".docx":
        pages = _load_docx(path)
    else:
        raise UnsupportedDocumentError(
            f"不支持的文件类型：{suffix or '无后缀'}（仅支持 {'、'.join(SUPPORTED_SUFFIXES)}）"
        )
    if not pages:
        raise UnsupportedDocumentError(f"{path.name} 中没有可提取的文字")
    return pages


def _load_pdf(path: Path) -> list[PageText]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [PageText(i + 1, (page.extract_text() or "").strip()) for i, page in enumerate(reader.pages)]
    total_chars = sum(len(p.text) for p in pages)
    # 用「平均每页字数」判断是不是扫描件：扫描件没有文字层，读出来几乎全是空的
    if pages and total_chars < MIN_CHARS_PER_PDF_PAGE * len(pages):
        raise UnsupportedDocumentError(
            f"{path.name} 平均每页可提取的文字不足 {MIN_CHARS_PER_PDF_PAGE} 字，疑似扫描件；当前不支持 OCR"
        )
    return [p for p in pages if p.text]


def _load_docx(path: Path) -> list[PageText]:
    import docx
    from docx.table import Table

    document = docx.Document(str(path))
    # python-docx >= 1.0 提供按文档顺序遍历段落与表格的 iter_inner_content；
    # 旧版本退化为「先段落后表格」，题目与答案的相对顺序可能被打乱
    if hasattr(document, "iter_inner_content"):
        blocks_in_order = document.iter_inner_content()
    else:
        blocks_in_order = [*document.paragraphs, *document.tables]

    blocks: list[str] = []
    for block in blocks_in_order:
        if isinstance(block, Table):
            for row in block.rows:
                line = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if line:
                    blocks.append(line)
        else:
            text = block.text.strip()
            if text:
                blocks.append(text)

    pages: list[PageText] = []
    buffer: list[str] = []
    size = 0
    for text in blocks:
        # 先判断再放：宁可让这一页略微超出上限，也不要把一个段落拦腰截断，
        # 题目被切成两半比超长更难处理
        if buffer and size + len(text) > DOCX_PAGE_CHARS:
            pages.append(PageText(len(pages) + 1, "\n".join(buffer)))
            buffer, size = [], 0
        buffer.append(text)
        size += len(text)
    if buffer:
        pages.append(PageText(len(pages) + 1, "\n".join(buffer)))
    return pages
