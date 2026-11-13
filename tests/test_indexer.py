from __future__ import annotations

import pytest
from docx import Document
from openpyxl import Workbook
from reportlab.pdfgen import canvas

from local_docs_mcp.extract import extract
from local_docs_mcp.indexer import scan
from local_docs_mcp.store import connect, get_document, search, stats


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    return root, tmp_path / "local_docs_mcp.sqlite"


def test_index_search_update_delete(tree):
    root, db = tree
    note = root / "hello.txt"
    note.write_text("contract renewable 2026", encoding="utf-8")
    summary = scan(str(root), db)
    assert summary["updated"] == 1
    with connect(db, readonly=True) as con:
        rows = search(con, '"contract renewable"')
        assert len(rows) == 1
        assert get_document(con, rows[0]["id"])["text"] == "contract renewable 2026"
        assert stats(con)["documents"] == 1
    assert scan(None, db)["unchanged"] == 1
    note.write_text("invoice signed in 2027", encoding="utf-8")
    assert scan(None, db)["updated"] == 1
    with connect(db, readonly=True) as con:
        assert not search(con, "renewable")
        assert len(search(con, "invoice")) == 1
    note.unlink()
    assert scan(None, db)["deletion_guard"] is True
    assert scan(None, db, allow_large_delete=True)["deleted"] == 1
    with connect(db, readonly=True) as con:
        assert stats(con)["documents"] == 0
        assert not search(con, "invoice")


def test_document_extractors(tree):
    root, db = tree
    word = Document()
    word.add_paragraph("Blue framework agreement")
    word.add_table(rows=1, cols=1).cell(0, 0).text = "contract tariff"
    word.save(root / "agreement.docx")
    book = Workbook()
    book.active["A1"] = "Budget forecast 2025"
    book.save(root / "budget.xlsx")
    pdf = canvas.Canvas(str(root / "letter.pdf"))
    pdf.drawString(50, 700, "Invoice 7711 is approved")
    pdf.save()
    assert "Blue framework" in extract(root / "agreement.docx")
    assert "Budget forecast" in extract(root / "budget.xlsx")
    assert "Invoice 7711" in extract(root / "letter.pdf")
    assert scan(str(root), db)["updated"] == 3
    with connect(db, readonly=True) as con:
        assert search(con, "agreement", ext="docx")
        assert search(con, "forecast", ext="xlsx")
        assert search(con, "invoice", ext="pdf")
        assert not search(con, "invoice", ext="docx")


def test_large_delete_guard(tree):
    root, db = tree
    for i in range(40):
        (root / f"{i}.txt").write_text(f"test number {i}", encoding="utf-8")
    assert scan(str(root), db)["updated"] == 40
    for i in range(20):
        (root / f"{i}.txt").unlink()
    result = scan(None, db)
    assert result["deletion_guard"]
    with connect(db, readonly=True) as con:
        assert stats(con)["documents"] == 40
    result = scan(None, db, allow_large_delete=True)
    assert not result["deletion_guard"]
    with connect(db, readonly=True) as con:
        assert stats(con)["documents"] == 20


def test_bad_query_and_invalid_ext(tree):
    root, db = tree
    (root / "t.txt").write_text("hello there", encoding="utf-8")
    scan(str(root), db)
    with connect(db, readonly=True) as con:
        with pytest.raises(ValueError, match="Invalid FTS5 query"):
            search(con, '"unterminated')
        with pytest.raises(ValueError, match="Unsupported extension"):
            search(con, "hello", ext=".exe")


def test_read_pagination(tree):
    root, db = tree
    (root / "t.txt").write_text("keyword " + "a" * 1000, encoding="utf-8")
    scan(str(root), db)
    with connect(db, readonly=True) as con:
        doc = search(con, "keyword")[0]
        page = get_document(con, doc["id"], max_chars=100, offset=500)
        assert page["text"] == "a" * 100
        assert page["truncated"]
