"""SQLite schema and read-only search interface."""
from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path


def default_db() -> Path:
    """Reuse a v0.3 Dropbox index if present; otherwise use the new app data directory.

    Set DOCINDEX_DB to force a specific database for worker, tray and MCP.
    """
    override = os.environ.get("DOCINDEX_DB")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home() / ".local" / "share"
    new = base / "local-docs-mcp" / "index.sqlite"
    legacy = base / "dropbox-doc-mcp" / "index.sqlite"
    return new if new.is_file() or not legacy.is_file() else legacy


def connect(db: Path | str, *, readonly: bool = False) -> sqlite3.Connection:
    db = Path(db).expanduser().resolve()
    if not readonly:
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), timeout=20)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    else:
        if not db.is_file():
            raise FileNotFoundError(f"Index not created yet: {db}. Run 'index' first.")
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=20)
        con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=20000")
    return con


def initialize(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
      name TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS documents (
      id INTEGER PRIMARY KEY,
      path TEXT NOT NULL UNIQUE,
      relpath TEXT NOT NULL,
      filename TEXT NOT NULL,
      ext TEXT NOT NULL,
      bytes INTEGER NOT NULL,
      mtime_ns INTEGER NOT NULL,
      body TEXT NOT NULL,
      indexed_ns INTEGER NOT NULL,
      scan_generation INTEGER NOT NULL,
      extract_profile TEXT NOT NULL DEFAULT '',
      root TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS documents_ext ON documents(ext);
    CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
      filename, relpath, body,
      content='documents', content_rowid='id',
      tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS doc_fts_insert AFTER INSERT ON documents BEGIN
      INSERT INTO document_fts(rowid, filename, relpath, body)
        VALUES (new.id, new.filename, new.relpath, new.body);
    END;
    CREATE TRIGGER IF NOT EXISTS doc_fts_delete AFTER DELETE ON documents BEGIN
      INSERT INTO document_fts(document_fts, rowid, filename, relpath, body)
        VALUES ('delete', old.id, old.filename, old.relpath, old.body);
    END;
    CREATE TRIGGER IF NOT EXISTS doc_fts_update AFTER UPDATE OF filename,relpath,body ON documents BEGIN
      INSERT INTO document_fts(document_fts, rowid, filename, relpath, body)
        VALUES ('delete', old.id, old.filename, old.relpath, old.body);
      INSERT INTO document_fts(rowid, filename, relpath, body)
        VALUES (new.id, new.filename, new.relpath, new.body);
    END;
    """)
    # Existing 0.1 databases predate OCR. Upgrade in place without rebuilding FTS
    # or changing document IDs; old PDF rows will be re-extracted on the next scan.
    columns = {row["name"] for row in con.execute("PRAGMA table_info(documents)")}
    if "extract_profile" not in columns:
        con.execute("ALTER TABLE documents ADD COLUMN extract_profile TEXT NOT NULL DEFAULT ''")
    if "root" not in columns:
        con.execute("ALTER TABLE documents ADD COLUMN root TEXT NOT NULL DEFAULT ''")
    # Upgrade a single-root v0.3 DB in place: no content or FTS rebuild.
    configured = get_setting(con, "root")
    if configured:
        if get_setting(con, "roots") is None:
            set_setting(con, "roots", json.dumps([configured]))
        con.execute("UPDATE documents SET root=? WHERE root=''", (configured,))
    con.execute("CREATE INDEX IF NOT EXISTS documents_root ON documents(root)")
    con.commit()


def get_setting(con: sqlite3.Connection, name: str) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
    return None if row is None else row["value"]


def set_setting(con: sqlite3.Connection, name: str, value: str) -> None:
    con.execute("INSERT INTO settings(name,value) VALUES (?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, value))


def get_roots(con: sqlite3.Connection) -> list[str]:
    """Return canonical configured root paths; old databases migrate lazily."""
    saved = get_setting(con, "roots")
    if saved:
        try:
            items = json.loads(saved)
            if isinstance(items, list) and all(isinstance(v, str) for v in items):
                return items
        except ValueError:
            raise ValueError("Corrupt roots configuration in database")
    old = get_setting(con, "root")
    return [old] if old else []


def set_roots(con: sqlite3.Connection, roots: list[str]) -> None:
    set_setting(con, "roots", json.dumps(roots))
    # Keep old tooling that reads settings.root functional.
    if roots:
        set_setting(con, "root", roots[0])
    else:
        con.execute("DELETE FROM settings WHERE name='root'")


def search(con: sqlite3.Connection, query: str, *, limit: int = 15,
           ext: str | None = None) -> list[dict]:
    if not query.strip():
        return []
    limit = max(1, min(int(limit), 50))
    extension = (ext or "").strip().lower().lstrip(".")
    if extension and extension not in {"pdf", "docx", "xlsx", "xlsm", "txt", "md", "csv"}:
        raise ValueError("Unsupported extension; choose pdf, docx, xlsx, xlsm, txt, md or csv.")
    sql = """
      SELECT d.id, d.filename, d.relpath, d.path, d.ext, d.bytes, d.mtime_ns,
             snippet(document_fts, 2, '[', ']', ' … ', 25) AS snippet,
             bm25(document_fts, 5.0, 2.0, 1.0) AS rank
      FROM document_fts JOIN documents d ON d.id=document_fts.rowid
      WHERE document_fts MATCH ?
    """
    args: list = [query]
    if extension:
        sql += " AND d.ext=?"
        args.append(extension)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    try:
        return [dict(row) for row in con.execute(sql, args).fetchall()]
    except sqlite3.OperationalError as exc:
        raise ValueError(f"Invalid FTS5 query: {exc}. Try quoting phrases, e.g. \"annual report\".") from exc


def get_document(con: sqlite3.Connection, doc_id: int, *, max_chars: int = 20000,
                 offset: int = 0) -> dict:
    max_chars = max(100, min(int(max_chars), 50000))
    offset = max(0, int(offset))
    row = con.execute("SELECT id,path,filename,relpath,ext,mtime_ns,body "
                      "FROM documents WHERE id=?", (int(doc_id),)).fetchone()
    if row is None:
        raise ValueError(f"Unknown document ID {doc_id}")
    result = {key: row[key] for key in ("id", "path", "filename", "relpath", "ext", "mtime_ns")}
    body = row["body"]
    result.update({"text": body[offset: offset + max_chars], "total_chars": len(body),
                   "offset": offset, "truncated": offset + max_chars < len(body)})
    return result


def stats(con: sqlite3.Connection) -> dict:
    row = con.execute("SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(body)),0) AS chars "
                      "FROM documents").fetchone()
    return {"documents": row["n"], "indexed_characters": row["chars"],
            "root": get_setting(con, "root"), "roots": get_roots(con),
            "last_completed_scan": get_setting(con, "last_completed_scan")}
