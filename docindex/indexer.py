"""Incremental filesystem scan, with a single-writer lock and deletion safety checks."""
from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable

from .extract import EXTENSIONS, extract, pdf_profile
from .dropbox import discover_dropbox
from .store import connect, get_roots, get_setting, initialize, set_roots, set_setting

LOG = logging.getLogger(__name__)
IGNORE_DIRS = {".git", ".svn", ".dropbox.cache", "node_modules", ".venv", "__pycache__"}


@contextlib.contextmanager
def single_writer(db: Path):
    """Cross-platform advisory lock. Retained for the duration of each scan."""
    db.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db.with_suffix(db.suffix + ".lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another document index worker is running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def register_root(con: sqlite3.Connection, path_str: str) -> Path:
    """Add a directory to the list of indexed roots without modifying other roots."""
    root = Path(path_str).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Directory does not exist: {root}")
    roots = [Path(value) for value in get_roots(con)]
    if root in roots:
        return root
    for existing in roots:
        if root.is_relative_to(existing) or existing.is_relative_to(root):
            raise ValueError(f"Overlapping roots are not supported: {root} and {existing}")
    set_roots(con, [*(str(value) for value in roots), str(root)])
    con.commit()
    return root


def resolve_roots(con: sqlite3.Connection, raw_root: str | None) -> list[Path]:
    if raw_root:
        register_root(con, raw_root)
    roots = get_roots(con)
    if not roots:
        if get_setting(con, "roots") is not None:
            raise ValueError("No roots configured. Register one with add-root PATH.")
        detected = discover_dropbox()
        register_root(con, str(detected))
        roots = get_roots(con)
    return [Path(root) for root in roots]


def remove_root(con: sqlite3.Connection, path_str: str, *, purge: bool = False) -> int:
    """Unregister a root, optionally removing its indexed documents from SQLite."""
    candidate = str(Path(path_str).expanduser().resolve())
    roots = get_roots(con)
    if candidate not in roots:
        raise ValueError(f"Root is not registered: {candidate}")
    deleted = 0
    if purge:
        deleted = con.execute("SELECT COUNT(*) FROM documents WHERE root=?", (candidate,)).fetchone()[0]
        con.execute("DELETE FROM documents WHERE root=?", (candidate,))
    set_roots(con, [root for root in roots if root != candidate])
    con.commit()
    return deleted


def scan(root_arg: str | None = None, db_arg: str | Path | None = None,
         *, allow_large_delete: bool = False,
         ocr_enabled: bool = True, ocr_languages: str = "nld+eng",
         reindex_pdfs: bool = False,
         stop_requested: Callable[[], bool] | None = None) -> dict:
    from .store import default_db
    db = Path(db_arg or default_db()).expanduser().resolve()
    with single_writer(db):
        con = connect(db)
        try:
            initialize(con)
            roots = resolve_roots(con, root_arg)
            generation = time.time_ns()
            totals = {"seen": 0, "updated": 0, "unchanged": 0, "errors": 0,
                      "deleted": 0, "deletion_guard": False, "interrupted": False,
                      "roots": len(roots), "skipped_roots": []}
            all_scanned = True
            for root in roots:
                if stop_requested and stop_requested():
                    totals["interrupted"] = True
                    break
                if not root.is_dir():
                    LOG.error("Indexed directory is offline: %s (retaining its records)", root)
                    totals["skipped_roots"].append(str(root))
                    totals["errors"] += 1
                    totals["deletion_guard"] = True
                    all_scanned = False
                    continue
                seen = updated = unchanged = failures = 0
                errors: list[OSError] = []
                interrupted = False

                def on_walk_error(error: OSError) -> None:
                    errors.append(error)
                    LOG.error("Cannot list folder %s: %s", root, error)

                for base, dirs, files in os.walk(root, followlinks=False, onerror=on_walk_error):
                    if stop_requested and stop_requested():
                        interrupted = True
                        break
                    dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not (Path(base) / d).is_symlink()]
                    for filename in files:
                        if stop_requested and stop_requested():
                            interrupted = True
                            break
                        path = Path(base) / filename
                        if path.suffix.lower() not in EXTENSIONS or filename.startswith("~$") or path.is_symlink():
                            continue
                        try:
                            st = path.stat()
                            rel = path.relative_to(root).as_posix()
                            raw = str(path)
                            seen += 1
                            old = con.execute("SELECT bytes,mtime_ns,extract_profile FROM documents WHERE path=?",
                                              (raw,)).fetchone()
                            profile = pdf_profile(ocr=ocr_enabled, languages=ocr_languages) if path.suffix.lower() == ".pdf" else ""
                            needs_reextract = path.suffix.lower() == ".pdf" and (
                                old is None or old["extract_profile"] != profile or reindex_pdfs)
                            if old is not None and (old["bytes"], old["mtime_ns"]) == (st.st_size, st.st_mtime_ns) and not needs_reextract:
                                con.execute("UPDATE documents SET scan_generation=?, root=? WHERE path=?",
                                            (generation, str(root), raw))
                                unchanged += 1
                            else:
                                body = extract(path, ocr=ocr_enabled, languages=ocr_languages)
                                con.execute("""INSERT INTO documents
                                    (path,relpath,filename,ext,bytes,mtime_ns,body,indexed_ns,
                                     scan_generation,extract_profile,root)
                                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                                    ON CONFLICT(path) DO UPDATE SET
                                    relpath=excluded.relpath, filename=excluded.filename,
                                    ext=excluded.ext, bytes=excluded.bytes, mtime_ns=excluded.mtime_ns,
                                    body=excluded.body, indexed_ns=excluded.indexed_ns,
                                    scan_generation=excluded.scan_generation,
                                    extract_profile=excluded.extract_profile, root=excluded.root""",
                                    (raw, rel, filename, path.suffix.lower().lstrip("."), st.st_size,
                                     st.st_mtime_ns, body, time.time_ns(), generation, profile, str(root)))
                                updated += 1
                        except Exception as exc:
                            # Corrupt/cloud-only files should keep any previous successfully indexed copy.
                            failures += 1
                            LOG.warning("Skipped %s: %s", path, exc)
                            con.execute("UPDATE documents SET scan_generation=? WHERE path=?",
                                        (generation, str(path)))
                        if seen % 100 == 0 and seen:
                            con.commit()
                            if seen % 1000 == 0:
                                LOG.info("%s: scanned %d, updated %d, errors %d", root, seen, updated, failures)
                    if interrupted:
                        break
                con.commit()
                totals["seen"] += seen
                totals["updated"] += updated
                totals["unchanged"] += unchanged
                totals["errors"] += failures + len(errors)
                if interrupted:
                    totals["interrupted"] = True
                    totals["deletion_guard"] = True
                    all_scanned = False
                    break
                if errors:
                    LOG.error("Could not traverse all of %s: preserving indexed files", root)
                    totals["deletion_guard"] = True
                    all_scanned = False
                    continue
                old_count = con.execute("SELECT COUNT(*) FROM documents WHERE root=?", (str(root),)).fetchone()[0]
                missing = con.execute("SELECT COUNT(*) FROM documents WHERE root=? AND scan_generation<>?",
                                      (str(root), generation)).fetchone()[0]
                if old_count and (seen == 0 or missing > max(5, int(old_count * 0.20))) and not allow_large_delete:
                    LOG.error("Safety guard for %s: %d/%d missing. Use --allow-large-delete if intentional.",
                              root, missing, old_count)
                    totals["deletion_guard"] = True
                    all_scanned = False
                    continue
                con.execute("DELETE FROM documents WHERE root=? AND scan_generation<>?", (str(root), generation))
                con.commit()
                totals["deleted"] += missing
            if all_scanned and not totals["interrupted"]:
                set_setting(con, "last_completed_scan", str(time.time()))
                con.commit()
            return totals
        finally:
            con.close()
