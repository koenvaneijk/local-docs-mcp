"""Regression tests for in-place upgrade, Dropbox discovery, and PDF OCR."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from local_docs_mcp.dropbox import discover_dropbox
from local_docs_mcp.extract import tesseract_languages, tesseract_executable
from local_docs_mcp.indexer import scan
from local_docs_mcp.store import connect, initialize, search, stats


def test_detect_current_user_dropbox(monkeypatch, tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    (root / "readme.txt").write_text("the current user owns this folder", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DROPBOX_DOC_ROOT", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert discover_dropbox() == root.resolve()
    db = tmp_path / "state.sqlite"
    result = scan(None, db)
    assert result["updated"] == 1
    with connect(db, readonly=True) as con:
        assert stats(con)["root"] == str(root.resolve())
        assert search(con, "current")


def test_detect_dropbox_info_json(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "emptyhome"))
    monkeypatch.delenv("DROPBOX_DOC_ROOT", raising=False)
    appdata = tmp_path / "AppData"
    location = tmp_path / "Dropbox (Personal)"
    location.mkdir()
    info = appdata / "Dropbox" / "info.json"
    info.parent.mkdir(parents=True)
    info.write_text(json.dumps({"personal": {"path": str(location)}}), encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert discover_dropbox() == location.resolve()


def test_multiple_dropbox_roots_require_explicit_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "absent"))
    monkeypatch.delenv("DROPBOX_DOC_ROOT", raising=False)
    config_dir = tmp_path / "AppData" / "Dropbox"
    config_dir.mkdir(parents=True)
    one, two = tmp_path / "Dropbox (Personal)", tmp_path / "Dropbox (Business)"
    one.mkdir()
    two.mkdir()
    (config_dir / "info.json").write_text(json.dumps({
        "personal": {"path": str(one)}, "business": {"path": str(two)}
    }), encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(config_dir.parent))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(ValueError, match="Several Dropbox folders"):
        discover_dropbox()


def test_schema_migration_preserves_document_id(tmp_path):
    db = tmp_path / "legacy.sqlite"
    with connect(db) as con:
        con.executescript("""
        CREATE TABLE documents (
          id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
          relpath TEXT NOT NULL, filename TEXT NOT NULL, ext TEXT NOT NULL,
          bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, body TEXT NOT NULL,
          indexed_ns INTEGER NOT NULL, scan_generation INTEGER NOT NULL
        );
        INSERT INTO documents VALUES (17, 'x.pdf', 'x.pdf', 'x.pdf', 'pdf', 100, 10, 'old content', 1, 0);
        """)
        initialize(con)
        row = con.execute("SELECT id,extract_profile FROM documents WHERE path='x.pdf'").fetchone()
        assert (row["id"], row["extract_profile"]) == (17, "")


def _make_image_pdf(pdf: Path) -> None:
    """PDF with an image of words, not a selectable text layer."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1500, 320), "white")
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 50)
    except OSError:
        font = ImageFont.load_default()
    draw.text((45, 90), "Factuur 9482 is betaald", font=font, fill="black")
    c = canvas.Canvas(str(pdf), pagesize=(750, 180))
    c.drawImage(ImageReader(im), 0, 0, width=750, height=180)
    c.save()


@pytest.mark.skipif(
    not {"nld", "eng"}.issubset(tesseract_languages(tesseract_executable())),
    reason="Tesseract with nld+eng needed for full OCR integration test",
)
def test_scanned_pdf_ocr_and_upgrade(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    _make_image_pdf(root / "scanned.pdf")
    db = tmp_path / "index.sqlite"
    assert scan(str(root), db, ocr_enabled=False)["updated"] == 1
    with connect(db, readonly=True) as con:
        assert not search(con, "9482")
    # Same unchanged PDF must be reprocessed after enabling OCR, without --root.
    result = scan(None, db)
    assert result["updated"] == 1
    with connect(db, readonly=True) as con:
        found = search(con, "9482")
        assert len(found) == 1
        id_before = found[0]["id"]
    assert scan(None, db)["unchanged"] == 1
    with connect(db, readonly=True) as con:
        assert search(con, "9482")[0]["id"] == id_before
