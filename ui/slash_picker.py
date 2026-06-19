"""Slash-command picker popover — Notion-style command palette."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from core.translations import tr
from ui.base_picker import SearchablePicker


def get_commands() -> list[tuple[str, str, str, str | None, str]]:
    """Return the list of slash commands with fresh translations."""
    headings = tr("Headings")
    inline = tr("Inline")
    blocks = tr("Blocks")
    links = tr("Links & Media")
    tasks = tr("Tasks")

    return [
        (tr("Heading 1"), "# ", headings, None, "h1"),
        (tr("Heading 2"), "## ", headings, None, "h2"),
        (tr("Heading 3"), "### ", headings, None, "h3"),
        (tr("Bold"), "**bold**", inline, None, "bold"),
        (tr("Italic"), "*italic*", inline, None, "italic"),
        (tr("Strikethrough"), "~~text~~", inline, None, "strikethrough"),
        (tr("Inline Code"), "`code`", inline, None, "inline-code"),
        (tr("Code Block"), "```\n\n```", blocks, None, "code-block"),
        (tr("Bullet List"), "- ", blocks, None, "bullet-list"),
        (tr("Numbered List"), "1. ", blocks, None, "numbered-list"),
        (tr("Block Quote"), "> ", blocks, None, "block-quote"),
        (tr("Divider"), "---\n", blocks, None, "divider"),
        (
            tr("Flashcard"),
            "```flashcard\nQuestion\n---\nAnswer\n```",
            blocks,
            None,
            "flashcard",
        ),
        (tr("Task / Checkbox"), "- [ ] ", tasks, None, "task"),
        (tr("Deadline"), "@deadline", tasks, tr("opens date picker"), "deadline"),
        (tr("External Link"), "[text](url)", links, None, "external-link"),
        (tr("Image"), "![alt](url)", links, None, "image"),
        (
            tr("Diagram"),
            "![diagram]()",
            blocks,
            tr("inserts interactive diagram"),
            "diagram",
        ),
        (
            tr("Table"),
            "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |",
            blocks,
            tr("inserts a pipe table"),
            "table",
        ),
    ]


class SlashPicker(SearchablePicker):
    """Searchable list of markdown commands — triggered by / in the editor."""

    def __init__(
        self,
        on_selected: Callable[[str, str, str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        self._on_selected_raw = on_selected
        super().__init__(
            items=get_commands(),
            on_selected=lambda row: None,  # handled in row_activated override
            text_view=text_view,
            placeholder=tr("Search commands"),
            width=280,
            height=320,
        )
        self.add_css_class("slash-picker")

    def _make_row(self, cmd: tuple[str, str, str, str | None]) -> Gtk.ListBoxRow:
        label, _insert, category, hint, _slug = cmd
        row = Gtk.ListBoxRow()
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_box.set_hexpand(True)

        name_label = Gtk.Label(label=label, xalign=0)
        name_label.add_css_class("slash-command-name")
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_box.append(name_label)

        desc_text = category
        if hint:
            desc_text += f" — {hint}"
        desc_label = Gtk.Label(label=desc_text, xalign=0)
        desc_label.add_css_class("slash-command-desc")
        desc_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_box.append(desc_label)

        row_box.append(text_box)

        cat_label = Gtk.Label(label=category)
        cat_label.add_css_class("slash-command-category")
        row_box.append(cat_label)

        row.set_child(row_box)
        row._cmd_data = cmd
        return row

    def _item_text(self, item: tuple[str, str, str, str | None]) -> str:
        return item[0] + " " + item[2]

    def _row_value(self, row: Gtk.ListBoxRow) -> tuple[str, str, str, str | None]:
        return row._cmd_data

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            self._on_selected_raw(row._cmd_data[0], row._cmd_data[1], row._cmd_data[4])
            self.popdown()
