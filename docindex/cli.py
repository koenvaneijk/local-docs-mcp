"""Manage multi-root local document indexing, OCR and full text search."""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .dropbox import discover_dropbox
from .extract import tesseract_executable, tesseract_languages
from .indexer import scan, single_writer, register_root, remove_root
from .store import connect, default_db, get_roots, initialize, search, stats


def _ocr_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR for scanned PDF pages")
    parser.add_argument("--ocr-languages", default="nld+eng", help="Tesseract language codes (default nld+eng)")


def _doctor(db_path: str) -> dict:
    path = Path(db_path).expanduser()
    executable = tesseract_executable()
    installed = tesseract_languages(executable)
    result = {"database": str(path), "tesseract": executable,
              "ocr_required_languages": ["nld", "eng"],
              "installed_ocr_languages": sorted(installed & {"nld", "eng"}),
              "ocr_ready": {"nld", "eng"}.issubset(installed)}
    roots = []
    if path.is_file():
        with connect(path, readonly=True) as con:
            try:
                roots = get_roots(con)
            except Exception as exc:
                result["database_error"] = str(exc)
    if not roots:
        try:
            roots = [str(discover_dropbox())]
            result["root_source"] = "autodetected-dropbox"
        except (OSError, ValueError) as exc:
            result["root_discovery_error"] = str(exc)
    else:
        result["root_source"] = "existing-index"
    result["roots"] = [{"path": root, "available": Path(root).is_dir()} for root in roots]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Local multi-folder full-text document index, with PDF OCR")
    parser.add_argument("--db", default=str(default_db()), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check configured roots and Tesseract languages")
    p = sub.add_parser("add-root", help="Register a directory to index alongside existing folders")
    p.add_argument("path")
    p = sub.add_parser("remove-root", help="Stop indexing a folder; optionally delete its indexed data")
    p.add_argument("path")
    p.add_argument("--purge", action="store_true", help="Also delete this folder's indexed records")
    sub.add_parser("roots", help="Show registered directories")
    p = sub.add_parser("index", help="Incrementally index all registered roots and reconcile deletions")
    p.add_argument("--root", help="Optionally register another root before indexing")
    p.add_argument("--allow-large-delete", action="store_true", help="Allow removal of >20%% missing files")
    p.add_argument("--reindex-pdfs", action="store_true", help="Re-extract unchanged PDFs")
    _ocr_flags(p)
    p = sub.add_parser("watch", help="Keep all registered roots indexed")
    p.add_argument("--root", help="Optionally register another root")
    p.add_argument("--interval", type=int, default=900, help="Time between scans in seconds (default 900)")
    _ocr_flags(p)
    sub.add_parser("status", help="Show indexed file count and roots")
    p = sub.add_parser("search", help="Search FTS5 index from a terminal")
    p.add_argument("query")
    p.add_argument("--type", dest="ext")
    p.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "doctor":
        print(json.dumps(_doctor(args.db), indent=2))
    elif args.command in {"add-root", "remove-root"}:
        db = Path(args.db).expanduser().resolve()
        with single_writer(db):
            with connect(db) as con:
                initialize(con)
                if args.command == "add-root":
                    added = register_root(con, args.path)
                    print(f"Added: {added}")
                else:
                    count = remove_root(con, args.path, purge=args.purge)
                    print(f"Removed: {args.path}. Purged {count} indexed documents." if args.purge
                          else f"Removed: {args.path}. Existing index records retained; use --purge to delete them.")
    elif args.command in {"index", "watch"}:
        kwargs = dict(ocr_enabled=not args.no_ocr, ocr_languages=args.ocr_languages)
        if args.command == "index":
            print(json.dumps(scan(args.root, args.db, allow_large_delete=args.allow_large_delete,
                                  reindex_pdfs=args.reindex_pdfs, **kwargs), indent=2))
        else:
            print("Watching local folders (Ctrl+C to stop).", flush=True)
            while True:
                try:
                    print(json.dumps(scan(args.root, args.db, **kwargs), indent=2), flush=True)
                except (OSError, ValueError, RuntimeError) as exc:
                    logging.error("Scan failed; will retry: %s", exc)
                time.sleep(max(60, args.interval))
    else:
        with connect(args.db, readonly=True) as con:
            if args.command == "status":
                print(json.dumps(stats(con), indent=2))
            elif args.command == "roots":
                print(json.dumps(get_roots(con), indent=2))
            elif args.command == "search":
                print(json.dumps(search(con, args.query, limit=args.limit, ext=args.ext), indent=2))


if __name__ == "__main__":
    main()
