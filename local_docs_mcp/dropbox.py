"""Find the current Windows user's locally synchronized Dropbox folder."""
from __future__ import annotations

import json
import os
from pathlib import Path


def discover_dropbox() -> Path:
    """Prefer ~/Dropbox, then Dropbox's own info.json account paths.

    Dropbox may be moved or renamed. When there are multiple accounts and no
    ~/Dropbox folder, selecting one silently is unsafe: ask for --root instead.
    """
    explicit = os.environ.get("DROPBOX_DOC_ROOT")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"DROPBOX_DOC_ROOT does not exist: {path}")
        return path

    home = Path.home()
    conventional = home / "Dropbox"
    if conventional.is_dir():
        return conventional.resolve()

    paths: set[Path] = set()
    for directory in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if not directory:
            continue
        config = Path(directory) / "Dropbox" / "info.json"
        if not config.is_file():
            continue
        try:
            info = json.loads(config.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for account in ("personal", "business"):
            data = info.get(account) or {}
            raw = data.get("path") if isinstance(data, dict) else None
            if isinstance(raw, str):
                path = Path(raw).expanduser()
                if path.is_dir():
                    paths.add(path.resolve())

    if len(paths) == 1:
        return next(iter(paths))
    if len(paths) > 1:
        raise ValueError("Several Dropbox folders found: " + ", ".join(map(str, sorted(paths))) +
                         ". Select one with --root, or set DROPBOX_DOC_ROOT.")
    raise FileNotFoundError("Cannot find a local Dropbox folder for this Windows user. "
                            "Try --root \"C:\\Users\\YOURNAME\\Dropbox\".")
