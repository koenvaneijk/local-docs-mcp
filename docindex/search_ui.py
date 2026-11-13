"""Small Tk search window. Runs in its own process, independent of the tray."""
from __future__ import annotations

import argparse
import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .opening import document_path, open_document
from .store import connect, default_db, search, stats

FILE_TYPES = {"All files": None, "PDF": "pdf", "Word": "docx", "Excel (.xlsx)": "xlsx",
              "Excel (.xlsm)": "xlsm", "Text": "txt", "Markdown": "md", "CSV": "csv"}


class SearchWindow:
    def __init__(self, root: tk.Tk, db: Path):
        self.root = root
        self.db = db
        self.results: dict[str, int] = {}
        self.snippets: dict[str, str] = {}
        root.title("Local Documents Search")
        root.geometry("1000x660")
        root.minsize(620, 400)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        top = ttk.Frame(frame)
        top.pack(fill="x")
        self.query = ttk.Entry(top)
        self.query.pack(side="left", fill="x", expand=True)
        self.query.bind("<Return>", self.perform_search)
        self.kind = tk.StringVar(value="All files")
        ttk.Combobox(top, values=list(FILE_TYPES), textvariable=self.kind, width=17,
                     state="readonly").pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Search", command=self.perform_search).pack(side="left", padx=(8, 0))

        ttk.Label(frame, text='FTS5: words, "exact phrase", OR, NOT and prefix*').pack(anchor="w", pady=(5, 6))
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=("name", "type", "folder"),
                                  show="headings", selectmode="browse")
        for key, label, width in (("name", "Filename", 330), ("type", "Type", 65),
                                  ("folder", "Folder", 420)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=(key != "type"))
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        self.table.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self.show_snippet)
        self.table.bind("<Double-1>", self.open_selected)
        ttk.Label(frame, text="Preview / matched text:").pack(anchor="w", pady=(10, 4))
        self.preview = tk.Text(frame, height=5, wrap="word", state="disabled")
        self.preview.pack(fill="x")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Open document", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="Show in Explorer", command=self.show_in_explorer).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Copy path", command=self.copy_path).pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value="Enter a query and press Enter.")
        ttk.Label(frame, textvariable=self.status).pack(side="right")
        try:
            with connect(self.db, readonly=True) as con:
                info = stats(con)
            self.status.set(f"{info['documents']:,} indexed files")
        except Exception as exc:
            self.status.set(f"Index unavailable: {exc}")
        root.after(100, self.query.focus_set)

    def _selection(self) -> int | None:
        items = self.table.selection()
        return self.results.get(items[0]) if items else None

    def perform_search(self, _event=None) -> None:
        phrase = self.query.get().strip()
        if not phrase:
            self.status.set("Enter a search phrase.")
            return
        try:
            with connect(self.db, readonly=True) as con:
                found = search(con, phrase, limit=50, ext=FILE_TYPES[self.kind.get()])
        except Exception as exc:
            self.status.set("Search failed")
            messagebox.showerror("Search error", str(exc), parent=self.root)
            return
        self.results.clear()
        self.snippets.clear()
        self.table.delete(*self.table.get_children())
        for row in found:
            iid = self.table.insert("", "end", values=(row["filename"], row["ext"],
                                                       str(Path(row["relpath"]).parent)))
            self.results[iid] = row["id"]
            self.table.set(iid, "name", row["filename"])
            self.snippets[iid] = str(row["snippet"] or "")
        self._preview("")
        self.status.set(f"{len(found)} results (maximum 50)")
        if found:
            first = self.table.get_children()[0]
            self.table.selection_set(first)
            self.table.focus(first)
            self.show_snippet()

    def _preview(self, snippet: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", snippet)
        self.preview.configure(state="disabled")

    def show_snippet(self, _event=None) -> None:
        selection = self.table.selection()
        if selection:
            self._preview(self.snippets.get(selection[0], ""))

    def _path(self) -> Path | None:
        doc_id = self._selection()
        if doc_id is None:
            return None
        with connect(self.db, readonly=True) as con:
            return document_path(con, doc_id)

    def open_selected(self, _event=None) -> None:
        doc_id = self._selection()
        if doc_id is None:
            return
        try:
            with connect(self.db, readonly=True) as con:
                open_document(con, doc_id)
        except Exception as exc:
            messagebox.showerror("Cannot open document", str(exc), parent=self.root)

    def show_in_explorer(self) -> None:
        try:
            path = self._path()
            if path:
                subprocess.Popen(["explorer.exe", "/select,", str(path)])
        except Exception as exc:
            messagebox.showerror("Cannot open Explorer", str(exc), parent=self.root)

    def copy_path(self) -> None:
        try:
            path = self._path()
            if path:
                self.root.clipboard_clear()
                self.root.clipboard_append(str(path))
                self.status.set("Path copied")
        except Exception as exc:
            messagebox.showerror("Cannot copy path", str(exc), parent=self.root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search local Dropbox document index")
    parser.add_argument("--db", default=os.environ.get("DOCINDEX_DB", str(default_db())))
    args = parser.parse_args()
    root = tk.Tk()
    SearchWindow(root, Path(args.db).expanduser())
    root.mainloop()


if __name__ == "__main__":
    main()
