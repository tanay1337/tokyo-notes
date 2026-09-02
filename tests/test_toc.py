"""Focused tests for Table of Contents update scheduling."""

from __future__ import annotations

from types import SimpleNamespace

import ui.toc as toc_module
from ui.toc import TocSidebar


def test_hidden_toc_only_marks_itself_dirty(monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(
        toc_module.GLib,
        "timeout_add",
        lambda *args: scheduled.append(args),
    )
    toc = SimpleNamespace(
        _dirty=False,
        _update_idle_id=0,
        get_mapped=lambda: False,
    )

    TocSidebar._on_buffer_changed(toc, object())

    assert toc._dirty
    assert scheduled == []


def test_mapping_dirty_toc_schedules_one_rebuild(monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(
        toc_module.GLib,
        "idle_add",
        lambda callback: (scheduled.append(callback), 19)[1],
    )
    toc = SimpleNamespace(_dirty=True, _update_idle_id=0, _rebuild=lambda: False)

    TocSidebar._on_map(toc, object())

    assert toc._update_idle_id == 19
    assert scheduled == [toc._rebuild]
