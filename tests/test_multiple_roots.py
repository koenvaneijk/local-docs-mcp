"""Generic document roots, legacy migrations and failure containment."""
from __future__ import annotations

import pytest

from docindex.indexer import scan, register_root, remove_root
from docindex.opening import document_path
from docindex.store import connect, initialize, get_roots, search, stats, default_db


def test_multiple_roots_index_and_independent_deletion(tmp_path):
    first = tmp_path / "Dropbox"
    second = tmp_path / "Accounting"
    first.mkdir(); second.mkdir()
    (first / "report.txt").write_text("first-root grape", encoding="utf8")
    (second / "notes.txt").write_text("second-root pear", encoding="utf8")
    db = tmp_path / "index.sqlite"
    assert scan(str(first), db)["updated"] == 1
    assert scan(str(second), db)["updated"] == 1
    with connect(db, readonly=True) as con:
        assert set(get_roots(con)) == {str(first.resolve()), str(second.resolve())}
        grape = search(con, "grape")[0]
        pear = search(con, "pear")[0]
        assert document_path(con, grape["id"]) == (first / "report.txt").resolve()
        assert document_path(con, pear["id"]) == (second / "notes.txt").resolve()
        assert stats(con)["documents"] == 2
    (first / "report.txt").unlink()
    assert scan(None, db)["deletion_guard"]  # cannot drop entire tiny root silently
    assert scan(None, db, allow_large_delete=True)["deleted"] == 1
    with connect(db, readonly=True) as con:
        assert not search(con, "grape")
        assert search(con, "pear")


def test_offline_root_preserves_docs_and_syncs_available_root(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "a.txt").write_text("fruits apple", encoding="utf8")
    (b / "b.txt").write_text("fruits banana", encoding="utf8")
    db = tmp_path / "index.sqlite"
    scan(str(a), db)
    scan(str(b), db)
    b.rename(tmp_path / "gone")
    (a / "a.txt").write_text("fruits watermelon", encoding="utf8")
    result = scan(None, db)
    assert result["deletion_guard"] and result["skipped_roots"] == [str(b.resolve())]
    with connect(db, readonly=True) as con:
        assert search(con, "watermelon")
        assert search(con, "banana")  # offline root is retained


def test_root_registration_and_removal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    nested = base / "nested"
    nested.mkdir()
    db = tmp_path / "index.sqlite"
    with connect(db) as con:
        initialize(con)
        register_root(con, str(base))
        assert register_root(con, str(base)) == base.resolve()
        with pytest.raises(ValueError, match="Overlapping"):
            register_root(con, str(nested))
        assert remove_root(con, str(base)) == 0
        assert get_roots(con) == []
    with pytest.raises(ValueError, match="No roots configured"):
        scan(None, db)


def test_legacy_db_reused_and_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DOCINDEX_DB", raising=False)
    base = tmp_path / "Dropbox"
    base.mkdir()
    (base / "f.txt").write_text("legacy quinoa", encoding="utf8")
    legacy = tmp_path / "dropbox-doc-mcp" / "index.sqlite"
    scan(str(base), legacy)
    with connect(legacy) as con:
        id_before = search(con, "quinoa")[0]["id"]
    assert default_db() == legacy
    with connect(legacy) as con:
        initialize(con)
        assert search(con, "quinoa")[0]["id"] == id_before
        assert get_roots(con) == [str(base.resolve())]


def test_migration_adds_root_to_pre_v4_database(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    db = tmp_path / "legacy.sqlite"
    with connect(db) as con:
        con.executescript("""
          CREATE TABLE settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE documents(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
            relpath TEXT NOT NULL,filename TEXT NOT NULL,ext TEXT NOT NULL,bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,body TEXT NOT NULL,indexed_ns INTEGER NOT NULL,
            scan_generation INTEGER NOT NULL,extract_profile TEXT NOT NULL DEFAULT '');
        """)
        con.execute("INSERT INTO settings VALUES('root',?)", (str(root),))
        con.execute("INSERT INTO documents VALUES(7,?,?,?,?,?,?,?,?,?,?)",
                    (str(root / 'x.txt'), 'x.txt', 'x.txt', 'txt', 3, 1, 'untouched', 1, 0, ''))
        initialize(con)
        got = con.execute('SELECT id,root FROM documents').fetchone()
        assert got['id'] == 7 and got['root'] == str(root)
        assert get_roots(con) == [str(root)]
