import pytest

from backend.agents.rag.ingest.loaders import DOCX_PAGE_CHARS, UnsupportedDocumentError, load_document
from backend.tests.agents.rag.helpers import write_docx, write_text_pdf


def test_pdf_text_is_extracted_per_page(tmp_path):
    path = tmp_path / "paper.pdf"
    write_text_pdf(path, "Solve the equation 2x + 3 = 7 and check your answer")
    pages = load_document(path)
    assert len(pages) == 1 and pages[0].number == 1
    assert "2x" in pages[0].text and "equation" in pages[0].text


def test_pdf_without_text_layer_is_rejected_as_scanned(tmp_path):
    path = tmp_path / "scan.pdf"
    write_text_pdf(path, "")
    with pytest.raises(UnsupportedDocumentError, match="扫描件"):
        load_document(path)


def test_docx_keeps_paragraph_and_table_order(tmp_path):
    path = tmp_path / "exercise.docx"
    write_docx(path, ["第一题：解方程 2x+3=7", "第二题：分解因式 x²-9"], table_rows=[["答案", "x=2"]])
    [page] = load_document(path)
    text = page.text
    assert text.index("第一题") < text.index("第二题") < text.index("答案 | x=2")


def test_docx_is_split_into_pages_of_bounded_size(tmp_path):
    path = tmp_path / "long.docx"
    write_docx(path, [f"第{i}题：" + "题干内容" * 50 for i in range(20)])
    pages = load_document(path)
    assert len(pages) > 1
    assert [p.number for p in pages] == list(range(1, len(pages) + 1))
    # 每页至多超出一个段落的长度
    assert all(len(p.text) <= DOCX_PAGE_CHARS + 250 for p in pages)


def test_empty_docx_is_rejected(tmp_path):
    path = tmp_path / "empty.docx"
    write_docx(path, [])
    with pytest.raises(UnsupportedDocumentError, match="没有可提取的文字"):
        load_document(path)


def test_unsupported_suffix_is_rejected(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(UnsupportedDocumentError, match="不支持的文件类型"):
        load_document(path)
