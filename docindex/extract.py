"""Bounded local document extraction; OCR low-text PDF pages with Tesseract."""
from __future__ import annotations

import csv
import logging
import os
import shutil
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)
EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv"}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_CHARS = 2_000_000
# Short page headers / an OCR layer consisting of only a page number should not
# prevent OCR of the scanned body beneath them.
MIN_PAGE_TEXT_CHARS = 80
OCR_DPI = 200
OCR_PAGE_TIMEOUT_SECONDS = 90
PDF_EXTRACT_PROFILE_VERSION = "pymupdf-ocr-1"


def pdf_profile(*, ocr: bool = True, languages: str = "nld+eng") -> str:
    return f"{PDF_EXTRACT_PROFILE_VERSION}:{'ocr-' + languages if ocr else 'no-ocr'}"


def tesseract_executable() -> str | None:
    """Find Tesseract even when the Windows installer did not set PATH."""
    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        return shutil.which(configured)
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        for base in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not base:
                continue
            prefix = Path(base)
            for candidate in (prefix / "Tesseract-OCR" / "tesseract.exe",
                              prefix / "Programs" / "Tesseract-OCR" / "tesseract.exe"):
                if candidate.is_file():
                    return str(candidate)
    return None


def tesseract_languages(executable: str | None = None) -> set[str]:
    executable = executable or tesseract_executable()
    if not executable:
        return set()
    try:
        proc = subprocess.run([executable, "--list-langs"], capture_output=True,
                              text=True, errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode:
        return set()
    return {line.strip() for line in (proc.stdout + "\n" + proc.stderr).splitlines()
            if line.strip() and not line.lower().startswith("list of available languages")
            and not line.lower().startswith("warning")}


def validate_ocr(languages: str = "nld+eng") -> str:
    executable = tesseract_executable()
    if not executable:
        raise RuntimeError("Tesseract not found. Install it, or set TESSERACT_CMD to its .exe path; "
                           "use --no-ocr to index only selectable PDF text.")
    requested = {part.strip() for part in languages.split("+") if part.strip()}
    if not requested:
        raise ValueError("OCR language list must not be empty")
    installed = tesseract_languages(executable)
    missing = requested - installed
    if missing:
        raise RuntimeError(f"Missing Tesseract language packs: {', '.join(sorted(missing))}. "
                           "Install .traineddata in Tesseract-OCR\\tessdata or change --ocr-languages.")
    return executable


def _ocr_page(page, *, executable: str, languages: str) -> str:
    import pymupdf

    # A4 at 200 DPI consumes ~4 MB grayscale, not the large RGB buffer.
    pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    png = pix.tobytes("png")
    del pix
    try:
        proc = subprocess.run([executable, "stdin", "stdout", "-l", languages], input=png,
                              capture_output=True, timeout=OCR_PAGE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Tesseract OCR timed out after {OCR_PAGE_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to execute Tesseract: {exc}") from exc
    if proc.returncode:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise RuntimeError(f"Tesseract OCR failed (exit {proc.returncode}): {detail}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def extract(path: Path, *, ocr: bool = True, languages: str = "nld+eng") -> str:
    ext = path.suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError(f"Unsupported document type {ext}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("File exceeds 100 MiB extraction limit")
    chunks: list[str] = []
    total = 0

    def push(value: object) -> bool:
        nonlocal total
        text = str(value).strip() if value is not None else ""
        if text and total < MAX_CHARS:
            trimmed = text[:MAX_CHARS - total]
            chunks.append(trimmed)
            total += len(trimmed) + 1
        return total < MAX_CHARS

    if ext == ".pdf":
        import pymupdf
        with pymupdf.open(str(path)) as document:
            if document.needs_pass:
                raise ValueError("Password-protected PDF; cannot read")
            executable: str | None = None
            for index, page in enumerate(document, start=1):
                native = page.get_text("text", sort=True).strip()
                page_text = native
                if ocr and len("".join(native.split())) < MIN_PAGE_TEXT_CHARS:
                    if executable is None:
                        executable = validate_ocr(languages)
                    recognized = _ocr_page(page, executable=executable, languages=languages)
                    # Keep native headers that the OCR engine might not recognize.
                    if recognized and native and native not in recognized:
                        page_text = f"{native}\n{recognized}"
                    elif recognized:
                        page_text = recognized
                if not push(f"[Page {index}]\n{page_text}"):
                    break
    elif ext == ".docx":
        from docx import Document
        doc = Document(str(path))
        for paragraph in doc.paragraphs:
            if not push(paragraph.text):
                break
        for table in doc.tables:
            for row in table.rows:
                if not push(" | ".join(cell.text for cell in row.cells)):
                    break
            if total >= MAX_CHARS:
                break
    elif ext in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            for ws in wb.worksheets:
                if not push(f"[Sheet: {ws.title}]"):
                    break
                for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    data = " | ".join(str(cell) for cell in row if cell is not None)
                    if data and not push(f"row {row_idx}: {data}"):
                        break
                if total >= MAX_CHARS:
                    break
        finally:
            wb.close()
    elif ext == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.reader(handle):
                if not push(" | ".join(row)):
                    break
    else:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            while total < MAX_CHARS:
                block = handle.read(min(65536, MAX_CHARS - total))
                if not block or not push(block):
                    break
    return "\n".join(chunks)
