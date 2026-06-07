"""Slash-command picker popover — Notion-style command palette."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from ui.base_picker import SearchablePicker

_HEADINGS = "Headings"
_INLINE = "Inline"
_BLOCKS = "Blocks"
_LINKS = "Links & Media"
_TASKS = "Tasks"

_COMMANDS: list[tuple[str, str, str, str | None]] = [
    ("Heading 1", "# ", _HEADINGS, None),
    ("Heading 2", "## ", _HEADINGS, None),
    ("Heading 3", "### ", _HEADINGS, None),
    ("Bold", "**bold**", _INLINE, None),
    ("Italic", "*italic*", _INLINE, None),
    ("Strikethrough", "~~text~~", _INLINE, None),
    ("Inline Code", "`code`", _INLINE, None),
    ("Code Block", "```\n\n```", _BLOCKS, None),
    ("Bullet List", "- ", _BLOCKS, None),
    ("Numbered List", "1. ", _BLOCKS, None),
    ("Block Quote", "> ", _BLOCKS, None),
    ("Divider", "---\n", _BLOCKS, None),
    ("Flashcard", "```flashcard\nQuestion\n---\nAnswer\n```", _BLOCKS, None),
    ("Task / Checkbox", "- [ ] ", _TASKS, None),
    ("Deadline", "@deadline", _TASKS, "opens date picker"),
    ("External Link", "[text](url)", _LINKS, None),
    ("Image", "![alt](url)", _LINKS, None),
]


class SlashPicker(SearchablePicker):
    """Searchable list of markdown commands — triggered by / in the editor."""

    def __init__(
        self,
        on_selected: Callable[[str, str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        self._on_selected_raw = on_selected
        super().__init__(
            items=_COMMANDS,
            on_selected=lambda row: None,  # handled in row_activated override
            text_view=text_view,
            placeholder="Search commands…",
            width=280,
            height=320,
        )
        self.add_css_class("slash-picker")

    def _make_row(self, cmd: tuple[str, str, str, str | None]) -> Gtk.ListBoxRow:
        label, _insert, category, hint = cmd
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
            self._on_selected_raw(*row._cmd_data[:2])
            self.popdown()
            GLib.idle_add(self.unparent)
