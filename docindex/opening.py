"""Validate and open original documents referenced by a local search index."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .store import get_document, get_roots


def document_path(con: sqlite3.Connection, doc_id: int) -> Path:
    """Return existing indexed file only if it is still within the indexed root.

    A path may have been moved/deleted since it was indexed, or a symlink may
    have changed. Resolve both paths before launching any external application.
    """
    document = get_document(con, doc_id, max_chars=100)
    roots = get_roots(con)
    if not roots:
        raise RuntimeError("Index has no configured document roots")
    path = Path(document["path"]).resolve(strict=True)
    if not path.is_file():
        raise RuntimeError("Indexed document is not a file")
    if not any(path.is_relative_to(Path(root).resolve(strict=True)) for root in roots if Path(root).is_dir()):
        raise RuntimeError("Indexed document is outside configured document folders")
    return path


def open_document(con: sqlite3.Connection, doc_id: int) -> Path:
    """Open document using the shell's default program on Windows."""
    if os.name != "nt":
        raise RuntimeError("Opening indexed files is supported on Windows only")
    path = document_path(con, doc_id)
    os.startfile(str(path))  # type: ignore[attr-defined]
    return path
