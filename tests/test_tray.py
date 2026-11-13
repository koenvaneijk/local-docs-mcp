"""Tray-specific unit tests that can run without a Windows desktop session."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from docindex import tray
from docindex.indexer import scan
from docindex.opening import document_path
from docindex.store import connect, search, stats


def test_startup_command_quotes_spaces(tmp_path):
    db = tmp_path / 'Dropbox Search' / 'index.sqlite'
    cmd = tray.startup_command(db, interval=900, languages='nld+eng', ocr=True)
    assert '"' in cmd
    assert 'Dropbox Search' in cmd
    assert '--ocr-languages nld+eng' in cmd
    assert '--no-ocr' not in cmd
    cmd_no_ocr = tray.startup_command(db, interval=75, languages='nld', ocr=False)
    assert '--no-ocr' in cmd_no_ocr
    assert '--interval 75' in cmd_no_ocr


def test_document_open_path_guard(tmp_path):
    root = tmp_path / 'Dropbox'
    root.mkdir()
    note = root / 'safe.txt'
    note.write_text('some unique searchable words', encoding='utf-8')
    db = tmp_path / 'index.sqlite'
    assert scan(str(root), db)['updated'] == 1
    with connect(db, readonly=True) as con:
        ident = search(con, 'unique')[0]['id']
        assert document_path(con, ident) == note.resolve()
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside', encoding='utf-8')
    with connect(db) as con:
        con.execute('UPDATE documents SET path=? WHERE id=?', (str(outside), ident))
        con.commit()
    with connect(db, readonly=True) as con:
        with pytest.raises(RuntimeError, match='outside configured document folders'):
            document_path(con, ident)


def test_interrupted_scan_does_not_reconcile_deletions(tmp_path):
    root = tmp_path / 'Dropbox'
    root.mkdir()
    for i in range(12):
        (root / f'{i:02}.txt').write_text(f'first scan contents {i}', encoding='utf-8')
    db = tmp_path / 'index.sqlite'
    scan(str(root), db)
    (root / '00.txt').unlink()
    ticks = [0]
    def request_stop():
        ticks[0] += 1
        return ticks[0] > 4
    result = scan(None, db, stop_requested=request_stop)
    assert result['interrupted']
    assert result['deletion_guard']
    with connect(db, readonly=True) as con:
        assert stats(con)['documents'] == 12
    assert scan(None, db)['deleted'] == 1


def test_controller_indexes_and_stops(monkeypatch, tmp_path):
    ran = threading.Event()
    settings = []

    def fake_scan(**kwargs):
        settings.append(kwargs)
        ran.set()
        return dict(seen=3, updated=1, unchanged=2, errors=0, deleted=0,
                    deletion_guard=False)

    monkeypatch.setattr(tray, 'scan', fake_scan)
    controller = tray.SyncController(tmp_path / 'db.sqlite', interval=60)
    controller.start()
    assert ran.wait(5)
    controller.stop()
    assert 'Last scan:' in controller.status
    assert len(settings) == 1
    assert callable(settings[0]['stop_requested'])


def test_controller_retries_error_after_explicit_wakeup(monkeypatch, tmp_path):
    ran = threading.Event()
    calls = [0]

    def fake_scan(**_kwargs):
        calls[0] += 1
        ran.set()
        if calls[0] == 1:
            raise RuntimeError('temporary failure')
        return dict(seen=1, updated=0, unchanged=1, errors=0, deleted=0,
                    deletion_guard=False)

    monkeypatch.setattr(tray, 'scan', fake_scan)
    controller = tray.SyncController(tmp_path / 'db.sqlite')
    controller.start()
    assert ran.wait(5)
    controller.index_now()
    for _ in range(200):
        if calls[0] >= 2:
            break
        threading.Event().wait(0.01)
    controller.stop()
    assert calls[0] >= 2
    assert 'Last scan:' in controller.status
