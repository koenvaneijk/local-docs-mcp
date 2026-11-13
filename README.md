# Local Docs MCP v0.4.0

Fast, local, **multi-folder document search** for Windows using SQLite FTS5, PDF OCR (Tesseract), a tray app, and a standalone MCP server for Goose Desktop.

Supports **PDF, DOCX, XLSX/XLSM, TXT, Markdown, CSV** (scanned PDFs get `nld+eng` OCR). No cloud services — Dropbox is only an autodetected default folder. Index any local folder(s) into one database. Separate from `outlook-classic-mcp`; does not touch Outlook mail.

## Requirements

- Windows 10/11, Python 3.11+ with `pip`, `venv`, `tkinter` (3.12/3.13 recommended)
- Files must be locally accessible (cloud *online-only* placeholders need downloading first)
- Optional: [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) with `nld` + `eng` for scanned PDFs
- Optional: Goose Desktop for MCP chat search

## Installation

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[tray]"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli doctor
.\.venv\Scripts\python.exe -m local_docs_mcp.cli index   # autodetects a local Dropbox
```

Any other folder (or add more):

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli add-root "D:\Documents"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli index
```

**Do not register nested/overlapping roots** (the indexer blocks this). Only index folders you're comfortable exposing to your local AI assistant.

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli roots
.\.venv\Scripts\python.exe -m local_docs_mcp.cli status
.\.venv\Scripts\python.exe -m local_docs_mcp.cli search '"annual report"'
```

DB location: `%LOCALAPPDATA%\local-docs-mcp\index.sqlite` (override with `LOCAL_DOCS_MCP_DB` or `--db`). It contains extracted text — keep it private.

## Tray app

```powershell
.\.venv\Scripts\pythonw.exe -m local_docs_mcp.tray
```

Tray icon → **Search documents…**; double-click a result to open it, or **Show in Explorer**. Enable **Start with Windows** if desired. Syncs after each scan and ~every 15 minutes; works without Goose. Offline roots keep their indexed rows while others sync. If launch fails, run `python.exe -m local_docs_mcp.tray` in a terminal (log: `tray.log` beside the DB).

**Do not run `watch` while the tray is running.** Console-only sync instead:

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli watch --interval 900
```

## Goose Desktop

Extensions → Add custom extension → **Standard IO**: Name `Local Docs Search`, Command your venv `python.exe`, Arguments `-m local_docs_mcp.server`.

Tools: `search_documents`, `read_document`, `open_document` (only on explicit request), `index_status`.

Example: “Find Word or PDF contracts mentioning a payment of €5,000, and open the best match.”

## Tesseract / OCR

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

Language packs (PowerShell **as Administrator**, default folder):

```powershell
$dest = 'C:\Program Files\Tesseract-OCR\tessdata'
Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/nld.traineddata' -OutFile "$dest\nld.traineddata"
Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata' -OutFile "$dest\eng.traineddata"
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
```

Low-text PDF pages are rendered and OCR'd locally. Set `TESSERACT_CMD` if installed elsewhere; disable with `index --no-ocr`; other languages via `--ocr-languages nld+eng+fra`. Changing OCR settings re-extracts PDFs once. Images inside DOCX/XLSX and legacy `.doc`/`.xls` are not OCR'd.

## Upgrading

1. Exit the old tray, stop any `watch` (the old indexer can delete v0.4 rows; disable its **Start with Windows** first).
2. Move your old `index.sqlite` to `%LOCALAPPDATA%\local-docs-mcp\`.
3. Extract, create/use the venv, `pip install -e ".[tray]"`.
4. `python -m local_docs_mcp.cli index` — migrates in place; IDs and FTS data preserved.
5. Restart the tray; existing Goose extension keeps working.

### Managing roots

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli add-root "E:\SharedFiles"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli index
.\.venv\Scripts\python.exe -m local_docs_mcp.cli remove-root "E:\SharedFiles"          # keeps records
.\.venv\Scripts\python.exe -m local_docs_mcp.cli remove-root "E:\SharedFiles" --purge  # also deletes records
```

Without `--purge`, indexed text is retained for recovery, but the opener refuses those docs (folder no longer authorized). To purge: re-add the root, then remove with `--purge`.

### Safety and limits

- Files open only inside an authorized root (symlinks resolved).
- Deletions >20% per root are blocked; confirm with `index --allow-large-delete` after checking files/sync client.
- Traversal errors and offline folders preserve records; other folders keep indexing.
- Indexing is size/mtime-based, not real-time. Deleted/moved files drop out on reconciliation; document IDs aren't stable after moves.
- Text capped at 2M chars / 100 MiB per file.
- The DB holds plaintext excerpts — keep it local and use trusted AI providers.

## Publishing

Commit source, `pyproject.toml`, README, tests. Never commit `.venv`, SQLite DBs, or logs.

```powershell
pip install "local-docs-mcp[tray] @ git+https://github.com/koenvaneijk/local-docs-mcp.git"
```

The `local_docs_mcp` module provides the CLI, tray, search UI, and MCP entry points.
