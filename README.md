# Local Docs MCP v0.4.0

Fast, local, **multi-folder document search** on Windows with SQLite FTS5, PDF OCR (Tesseract), a tray app, and a standalone MCP server for Goose Desktop.

Supports **PDF, DOCX, XLSX/XLSM, TXT, Markdown, CSV**. Scanned PDF pages can use Dutch+English OCR (`nld+eng`). There is **no Dropbox API** or cloud service; Dropbox is only a convenient *autodetected default folder*. You can index any local folder or multiple folders into one database.

This is separate from **`outlook-classic-mcp`**. It does not index or modify Outlook mail.

## Requirements

- Windows 10/11; standard Python 3.11+ with `pip`, `venv` and `tkinter` (Python 3.12/3.13 recommended).
- Local files must be accessible. Dropbox, OneDrive or other cloud-synced *online-only* placeholders may not be readable until downloaded.
- Optional: [Tesseract OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki) with `nld` + `eng` traineddata. Enable this if you want scanned PDFs searchable.
- Optional: Goose Desktop for MCP-based chat search.

## Installation (new Windows PC)

In PowerShell, from the extracted `local-docs-mcp` directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[tray]"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli doctor
```

If you have a local `C:\Users\<username>\Dropbox` folder or Dropbox's `info.json`, the first scan can autodetect it:

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli index
```

For any other folder (or to add a second indexed folder):

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli add-root "D:\Documents"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli add-root "C:\Users\YourName\Dropbox"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli index
```

**Do not register nested or overlapping roots.** The indexer prevents this. Only register folders you are comfortable exposing to your local AI assistant.

Check/search:

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli roots
.\.venv\Scripts\python.exe -m local_docs_mcp.cli status
.\.venv\Scripts\python.exe -m local_docs_mcp.cli search '"annual report"'
```

The index is stored at `%LOCALAPPDATA%\local-docs-mcp\index.sqlite`. Override with `LOCAL_DOCS_MCP_DB` or `--db` (CLI). Keep the DB private; it contains extracted document text.

## Tray app / background sync

```powershell
.\.venv\Scripts\pythonw.exe -m local_docs_mcp.tray
```

Use the tray icon → **Search documents…**. Double-click a result to open the original, or choose **Show in Explorer**. Enable **Start with Windows** in its menu if desired. The tray syncs immediately and approximately every 15 minutes *after a scan finishes*. It does not need Goose running. If one configured root is offline, its existing indexed rows are retained while the other roots continue syncing.

If tray launch fails, run `python.exe -m local_docs_mcp.tray` in a visible terminal to see the error. The log is beside your SQLite DB (`tray.log`). The search UI requires Tkinter (`python -m tkinter`).

**Do not run `watch` concurrently with the tray**. For console-only background sync instead:

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli watch --interval 900
```

## Goose Desktop MCP configuration

Goose → Extensions → Add custom extension → **Standard IO**:

- **Name:** Local Docs Search
- **Command:** `C:\FULL\PATH\TO\local-docs-mcp\.venv\Scripts\python.exe`
- **Arguments:** `-m local_docs_mcp.server`

Tools: `search_documents`, `read_document`, `open_document` (only opens when the user explicitly requests it), `index_status`.

For example: “Find Word or PDF contracts mentioning a payment of €5,000, and open the best match.”

## Tesseract / OCR

Install from PowerShell:

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

To install language packs if missing (PowerShell **as Administrator**, default installation folder):

```powershell
$dest = 'C:\Program Files\Tesseract-OCR\tessdata'
Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/nld.traineddata' -OutFile "$dest\nld.traineddata"
Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata' -OutFile "$dest\eng.traineddata"
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
```

PDF pages with little selectable text are rendered and OCR'd locally. If Tesseract is somewhere else, set `TESSERACT_CMD` to its `.exe` path. Disable OCR with `index --no-ocr`, or set other languages with `--ocr-languages nld+eng+fra`. Changing OCR configuration re-extracts existing PDFs once; other unchanged documents are skipped. Embedded pictures inside DOCX/XLSX and older `.doc` / `.xls` files aren't OCR'd.

## Upgrading from an older build

1. **Exit the old tray and stop any `watch` process.** The older indexer does not understand multiple roots and can delete rows added under v0.4. If you enabled the old **Start with Windows**, disable it in the old tray first.
2. With processes stopped, move your old `index.sqlite` into `%LOCALAPPDATA%\local-docs-mcp\`.
3. Extract `local-docs-mcp` and create/use a venv, then `pip install -e ".[tray]"` from this directory.
4. Run `python -m local_docs_mcp.cli index`. This migrates the existing database **in place** without reindexing unchanged files; document IDs and FTS data are preserved.
5. Restart the tray. Your existing Goose extension can continue using `-m local_docs_mcp.server` provided its Python environment points to the upgraded package. No change to the Outlook MCP is needed.

### Managing folder roots

```powershell
.\.venv\Scripts\python.exe -m local_docs_mcp.cli add-root "E:\SharedFiles"
.\.venv\Scripts\python.exe -m local_docs_mcp.cli index
.\.venv\Scripts\python.exe -m local_docs_mcp.cli remove-root "E:\SharedFiles"          # retains searchable records
.\.venv\Scripts\python.exe -m local_docs_mcp.cli remove-root "E:\SharedFiles" --purge  # delete indexed records too
```

`remove-root` without `--purge` keeps existing indexed text for recovery, but the opener will refuse those documents because the folder is no longer authorized. To permanently clear those records, re-add the root then remove it with `--purge`.

### Safety and limits

- Files are opened only when they still exist **inside an authorized root**, including symlink resolution.
- Large unexpected deletions (over 20% per root) are blocked by default. Explicitly confirm with `index --allow-large-delete` *after checking your files and sync client*.
- Traversal errors and offline folders preserve existing records, and other configured folders can continue indexing.
- Indexing is size/mtime based, not real-time filesystem events. Deleting or moving a file will cause it to be removed from the index on a successful reconciliation; its numeric document ID is **not** a stable identifier after moves.
- Extracted text is capped at 2 million characters and 100 MiB per file. PDFs with very little text may incur OCR CPU cost.
- The database contains plaintext excerpts. Keep it local and use trusted AI providers if sending document contents to a model.

## Publishing to a public GitHub repository

Commit source files, `pyproject.toml`, README and tests. Do **not** commit `.venv`, any SQLite database or logs. Users can install with:

```powershell
pip install "local-docs-mcp[tray] @ git+https://github.com/koenvaneijk/local-docs-mcp.git"
```

The `local_docs_mcp` Python module provides the CLI, tray, search UI and MCP entry points.
