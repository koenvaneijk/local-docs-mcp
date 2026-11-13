"""Read-only MCP interface to the local document index, plus open-original action."""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .store import connect, default_db, get_document, search, stats
from .opening import open_document as open_indexed_document

mcp = FastMCP("Local Documents Search")
DB = Path(os.environ.get("DOCINDEX_DB", str(default_db()))).expanduser()


@mcp.tool()
def search_documents(query: str, limit: int = 15, file_type: str = "") -> list[dict]:
    """Search indexed local files using SQLite FTS5.

    Supports terms (both words must appear: `invoice payment`), exact phrases
    (`"annual accounts"`), OR (`invoice OR receipt`), NOT (`report NOT draft`),
    prefixes (`invoic*`). file_type: pdf, docx, xlsx, xlsm, txt, csv, md.
    Returns document IDs, filenames, paths, snippets and relevance scores.
    """
    with connect(DB, readonly=True) as con:
        return search(con, query, limit=limit, ext=file_type)


@mcp.tool()
def read_document(document_id: int, offset: int = 0, max_chars: int = 20000) -> dict:
    """Read indexed text from a search result by document ID; paginate long files with offset."""
    with connect(DB, readonly=True) as con:
        return get_document(con, document_id, offset=offset, max_chars=max_chars)


@mcp.tool()
def open_document(document_id: int) -> str:
    """Open the original indexed file with the default Windows application.

    Only call this when the user explicitly requests opening a specific file.
    """
    with connect(DB, readonly=True) as con:
        path = open_indexed_document(con, document_id)
    return f"Opened {path.name} in its default application."


@mcp.tool()
def index_status() -> dict:
    """Return indexed document count, root folders, and last successful full scan timestamp."""
    with connect(DB, readonly=True) as con:
        return stats(con)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
