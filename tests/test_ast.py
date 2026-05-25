"""Tests for the markdown AST module."""

from __future__ import annotations

from markdown.ast import MdLine


def test_mdline_defaults() -> None:
    line = MdLine(raw="hello", kind="text")
    assert line.raw == "hello"
    assert line.kind == "text"
    assert line.level == 0
    assert line.meta is None


def test_mdline_heading() -> None:
    line = MdLine(raw="# Title", kind="heading", level=1, text="Title")
    assert line.raw == "# Title"
    assert line.kind == "heading"
    assert line.level == 1


def test_mdline_task() -> None:
    line = MdLine(
        raw="- [x] done",
        kind="task",
        marker="- [x]",
        text="done",
    )
    assert line.kind == "task"


def test_mdline_blank() -> None:
    line = MdLine.blank()
    assert line.kind == "blank"
    assert line.raw == ""
