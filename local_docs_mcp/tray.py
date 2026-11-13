"""Windows notification-area app: background sync + standalone search window.

Run with: .venv\\Scripts\\pythonw.exe -m local_docs_mcp.tray
No UI toolkit is imported in the scan thread; search uses a separate Tk process.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .indexer import scan, single_writer
from .store import default_db

LOG = logging.getLogger(__name__)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Local Documents Search MCP"


def preferred_pythonw(executable: str | Path | None = None) -> Path:
    """Use current venv's pythonw.exe, so clicking Search never opens a terminal."""
    current = Path(executable or sys.executable)
    if os.name == "nt":
        gui = current.with_name("pythonw.exe")
        if gui.is_file():
            return gui
    return current


def launch_command(db: Path, *, interval: int, languages: str, ocr: bool) -> list[str]:
    cmd = [str(preferred_pythonw()), "-m", "local_docs_mcp.tray", "--db", str(db),
           "--interval", str(interval), "--ocr-languages", languages]
    if not ocr:
        cmd.append("--no-ocr")
    return cmd


def startup_command(db: Path, *, interval: int, languages: str, ocr: bool) -> str:
    return subprocess.list2cmdline(launch_command(db, interval=interval,
                                                  languages=languages, ocr=ocr))


def is_startup_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except FileNotFoundError:
        return False


def set_startup(enabled: bool, command: str) -> None:
    """HKCU only: never needs administrator access."""
    if os.name != "nt":
        raise RuntimeError("Windows autostart is only supported on Windows")
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def configure_logging(db: Path) -> Path:
    log_dir = db.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "tray.log"
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return path


class SyncController:
    """One background sync thread; never blocks tray menu or search UI."""
    def __init__(self, db: Path, *, interval: int = 900,
                 languages: str = "nld+eng", ocr: bool = True):
        self.db = db
        self.interval = max(60, interval)
        self.languages = languages
        self.ocr = ocr
        self.status = "Starting…"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="Local Docs Index Sync", daemon=True)
        self.thread.start()

    def index_now(self) -> None:
        self._wake.set()
        if self.status != "Indexing…":
            self.status = "Index requested…"

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.status = "Indexing…"
            try:
                result = scan(db_arg=self.db, ocr_enabled=self.ocr,
                              ocr_languages=self.languages, stop_requested=self._stop.is_set)
                if result.get("interrupted"):
                    self.status = "Indexing interrupted"
                    return
                if result.get("deletion_guard"):
                    self.status = "Sync warning: incomplete scan; no deletions"
                else:
                    self.status = (f"Last scan: {result['seen']:,} files, "
                                   f"{result['updated']:,} updated, "
                                   f"{result['errors']:,} errors")
                LOG.info("Documents sync: %s", result)
            except Exception as exc:
                self.status = f"Sync failed: {str(exc)[:120]}"
                LOG.exception("Document sync failed; will retry on next interval")
            self._wake.wait(self.interval)


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((11, 5, 48, 57), radius=5, fill=(48, 123, 214, 255))
    draw.line([(18, 20), (41, 20)], fill="white", width=3)
    draw.line([(18, 30), (38, 30)], fill="white", width=3)
    draw.line([(18, 40), (31, 40)], fill="white", width=3)
    draw.ellipse((35, 34, 54, 53), outline="white", width=4)
    draw.line((50, 50, 60, 60), fill="white", width=4)
    return img


def _show_error(title: str, detail: str) -> None:
    LOG.error("%s: %s", title, detail)
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, detail, title, 0x10)
    else:
        print(f"{title}: {detail}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Windows tray local document indexer and search")
    parser.add_argument("--db", default=os.environ.get("LOCAL_DOCS_MCP_DB", str(default_db())))
    parser.add_argument("--interval", type=int, default=900, help="Seconds between scans; minimum 60")
    parser.add_argument("--ocr-languages", default="nld+eng")
    parser.add_argument("--no-ocr", action="store_true")
    args = parser.parse_args()
    db = Path(args.db).expanduser().resolve()
    log_path = configure_logging(db)
    if os.name != "nt":
        raise SystemExit("The tray app currently supports Windows only. Use 'local_docs_mcp.cli watch' elsewhere.")

    try:
        with single_writer(db.with_name("tray.instance")):
            _run_icon(db, args.interval, args.ocr_languages, not args.no_ocr, log_path)
    except RuntimeError as exc:
        _show_error("Local Documents Search", str(exc))


def _run_icon(db: Path, interval: int, languages: str, ocr: bool, log_path: Path) -> None:
    try:
        import pystray
    except ImportError as exc:
        _show_error("Missing tray dependency", "Install with: pip install -e \".[tray]\"")
        raise SystemExit(2) from exc

    controller = SyncController(db, interval=interval, languages=languages, ocr=ocr)
    start_cmd = startup_command(db, interval=interval, languages=languages, ocr=ocr)
    icon = None

    def search_window(_icon=None, _item=None) -> None:
        try:
            cmd = [str(preferred_pythonw()), "-m", "local_docs_mcp.search_ui", "--db", str(db)]
            subprocess.Popen(cmd, cwd=str(db.parent), close_fds=True)
        except Exception as exc:
            _show_error("Cannot open search", str(exc))

    def request_sync(_icon=None, _item=None) -> None:
        controller.index_now()
        LOG.info("Manual sync requested")

    def toggle_autostart(_icon=None, _item=None) -> None:
        try:
            set_startup(not is_startup_enabled(), start_cmd)
        except Exception as exc:
            _show_error("Startup setting failed", str(exc))

    def open_root(_icon=None, _item=None) -> None:
        try:
            from .store import connect, get_roots
            with connect(db, readonly=True) as con:
                roots = get_roots(con)
            accessible = [root for root in roots if Path(root).is_dir()]
            if not accessible:
                raise FileNotFoundError("No indexed document folder is currently available")
            os.startfile(accessible[0])
        except Exception as exc:
            _show_error("Cannot open folder", str(exc))

    def open_log(_icon=None, _item=None) -> None:
        os.startfile(str(log_path))

    def quit_app(_icon=None, _item=None) -> None:
        controller.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Search documents…", search_window, default=True),
        pystray.MenuItem("Index now", request_sync),
        pystray.MenuItem(lambda _: controller.status, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open first indexed folder", open_root),
        pystray.MenuItem("Open sync log", open_log),
        pystray.MenuItem("Start with Windows", toggle_autostart,
                         checked=lambda _: is_startup_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", quit_app),
    )
    icon = pystray.Icon("local_docs_search", _icon_image(), "Local Documents Search", menu)
    controller.start()
    try:
        icon.run()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
