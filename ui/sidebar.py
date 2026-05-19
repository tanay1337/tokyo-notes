"""Sidebar UI component — note list, search, and navigation footer."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango

from core.utils import create_empty_state_widget

if TYPE_CHECKING:
    from main import TokyoNotes


@functools.lru_cache(maxsize=1)
def _get_pin_icon_name() -> str:
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    return "pin-symbolic" if theme.has_icon("pin-symbolic") else "view-pin-symbolic"


class Sidebar(Gtk.Box):
    """Vertical sidebar: header, search entry, note lists, and nav footer."""

    def __init__(
        self,
        app: "TokyoNotes",
        on_new_note: Callable[..., Any],
        on_dashboard_clicked: Callable[..., Any],
        on_archive_clicked: Callable[..., Any],
        on_graph_clicked: Callable[..., Any],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.add_css_class("sidebar")

        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label="Tokyo Notes"))
        new_btn = Gtk.Button(icon_name="document-new-symbolic")
        new_btn.connect("clicked", on_new_note)
        sidebar_header.pack_start(new_btn)
        self.append(sidebar_header)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search notes…")
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("stop-search", lambda _: self.app.refresh_list())
        self.append(self.search_entry)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.main_list = Gtk.ListBox()
        self.stack.add_named(self.main_list, "main")
        self.archive_list = Gtk.ListBox()
        self.stack.add_named(self.archive_list, "archive")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.stack)
        self.append(scrolled)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)

        self.archived_nav_btn = Gtk.Button(label="Archived Notes")
        self.archived_nav_btn.add_css_class("archived-nav-btn")
        self.archived_nav_btn.connect("clicked", on_archive_clicked)
        self.archived_nav_btn.set_sensitive(False)
        footer.append(self.archived_nav_btn)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self._dashboard_btn = Gtk.Button(label="Dashboard")
        self._dashboard_btn.set_hexpand(True)
        self._dashboard_btn.connect("clicked", on_dashboard_clicked)
        self._dashboard_btn.add_css_class("dashboard-footer-btn")
        btn_row.append(self._dashboard_btn)

        self._graph_btn = Gtk.Button(label="Graph")
        self._graph_btn.set_hexpand(True)
        self._graph_btn.connect("clicked", lambda _: on_graph_clicked())
        self._graph_btn.add_css_class("dashboard-footer-btn")
        btn_row.append(self._graph_btn)

        footer.append(btn_row)
        self.append(footer)

        self._nav_buttons = {
            "dashboard": self._dashboard_btn,
            "graph":     self._graph_btn,
        }

    # Search

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self.app.search.on_search_changed(entry)

    # Active-view indicator

    def set_active_view(self, view: str) -> None:
        """Highlight the footer button matching *view*; clear all others."""
        for name, btn in self._nav_buttons.items():
            if name == view:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    # Archive helpers

    def maybe_exit_archive_view(self) -> None:
        if (
            not self.app.cfg.archived
            and self.stack.get_visible_child_name() == "archive"
        ):
            self.stack.set_visible_child_name("main")
            self.archived_nav_btn.set_label("Archived Notes")
        self.archived_nav_btn.set_sensitive(bool(self.app.cfg.archived))

    def toggle_archive_view(self) -> None:
        if self.stack.get_visible_child_name() == "archive":
            self.stack.set_visible_child_name("main")
            self.archived_nav_btn.set_label("Archived Notes")
        else:
            self.stack.set_visible_child_name("archive")
            self.archived_nav_btn.set_label("Back to Notes")

    def on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        visible = button.get_active()
        self.app.split_view.set_show_sidebar(visible)
        self.app.cfg.set("show_sidebar", visible)

    # Populate

    def populate(
        self,
        main_notes: list[str],
        pinned: set[str],
        archived_notes: set[str],
        on_right_click: Callable[..., Any],
        snippet_fn: Callable[[str], str],
        base_dir: Path,
        filter_text: str = "",
    ) -> None:
        """Rebuild both list boxes from scratch."""
        self._clear(self.main_list)
        self._clear(self.archive_list)

        pinned_notes = [n for n in main_notes if n in pinned]
        other_notes  = [n for n in main_notes if n not in pinned]

        for note in pinned_notes:
            self.main_list.append(
                self._make_row(note, snippet_fn(note), is_pinned=True,
                               on_right_click=on_right_click, base_dir=base_dir)
            )
        for note in other_notes:
            self.main_list.append(
                self._make_row(note, snippet_fn(note),
                               on_right_click=on_right_click, base_dir=base_dir)
            )

        if not pinned_notes and not other_notes:
            msg = "No notes match." if filter_text else "No notes yet."
            self.main_list.append(create_empty_state_widget(msg, base_dir))

        for note in archived_notes:
            if filter_text:
                fl = filter_text.lower()
                snippet = snippet_fn(note)
                if fl not in note.lower() and fl not in snippet.lower():
                    continue
            else:
                snippet = snippet_fn(note)
            self.archive_list.append(
                self._make_row(note, snippet, is_archived=True,
                               on_right_click=on_right_click, base_dir=base_dir)
            )

        self.archived_nav_btn.set_sensitive(bool(archived_notes))

    # Internal helpers

    def _clear(self, lb: Gtk.ListBox) -> None:
        while (child := lb.get_first_child()):
            lb.remove(child)

    def _make_row(
        self,
        note_name: str,
        snippet_text: str,
        is_pinned: bool = False,
        is_archived: bool = False,
        on_right_click: Callable[..., Any] | None = None,
        base_dir: Path | None = None,
    ) -> Gtk.ListBoxRow:
        """Build a single sidebar row. Attach hover-preload for instant switching."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        label = Gtk.Label(label=note_name, xalign=0)
        label.add_css_class("sidebar-label")
        if is_archived:
            label.add_css_class("muted-label")
        label.set_hexpand(True)
        title_box.append(label)

        pin_icon = Gtk.Image()
        pin_icon.set_from_icon_name(_get_pin_icon_name())
        pin_icon.set_visible(is_pinned)
        title_box.append(pin_icon)

        box.append(title_box)

        snippet = Gtk.Label(label=snippet_text, xalign=0)
        snippet.add_css_class("sidebar-snippet")
        snippet.set_ellipsize(Pango.EllipsizeMode.END)
        snippet.set_hexpand(True)
        box.append(snippet)

        row.set_child(box)
        row.note_name = note_name
        row.title_label = label
        row.snippet_label = snippet

        # Hover-preload using a weakref so the row can be GCed.
        import weakref
        _app_ref = weakref.ref(self.app)
        hover = Gtk.EventControllerMotion()
        def _on_hover_enter(*_):
            app = _app_ref()
            if app is not None:
                app.notes_manager.read_note(note_name)
        hover.connect("enter", _on_hover_enter)
        row.add_controller(hover)

        if on_right_click:
            gesture = Gtk.GestureClick(button=3)
            gesture.connect("pressed", on_right_click, row, is_archived)
            row.add_controller(gesture)

        return row
