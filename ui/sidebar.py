"""Sidebar UI component for note listing and navigation."""
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
    """Return the best available pin icon name (result cached after first call)."""
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    return "pin-symbolic" if theme.has_icon("pin-symbolic") else "view-pin-symbolic"


class Sidebar(Gtk.Box):
    """Vertical sidebar containing the note list, search bar, and navigation footer."""

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

        # ---- Header ----
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label="Tokyo Notes"))
        new_note_btn = Gtk.Button(icon_name="document-new-symbolic")
        new_note_btn.connect("clicked", on_new_note)
        sidebar_header.pack_start(new_note_btn)
        self.append(sidebar_header)

        # ---- Search ----
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search notes...")
        self.search_entry.connect("search-changed", self.on_search_changed)
        # "stop-search" fires when the user presses Escape inside the entry.
        # GTK clears the text natively; we trigger a list refresh here.
        self.search_entry.connect("stop-search", lambda _: self.app.refresh_list())
        self.append(self.search_entry)

        # ---- Note lists (main + archive in a Stack) ----
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)

        self.main_list = Gtk.ListBox()
        self.stack.add_named(self.main_list, "main")

        self.archive_list = Gtk.ListBox()
        self.stack.add_named(self.archive_list, "archive")

        scrolled_list = Gtk.ScrolledWindow()
        scrolled_list.set_child(self.stack)
        self.append(scrolled_list)

        # ---- Footer ----
        footer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        footer_box.set_margin_start(10)
        footer_box.set_margin_end(10)
        footer_box.set_margin_top(10)
        footer_box.set_margin_bottom(10)

        self.archived_nav_btn = Gtk.Button(label="Archived Notes")
        self.archived_nav_btn.add_css_class("archived-nav-btn")
        self.archived_nav_btn.connect("clicked", on_archive_clicked)
        # Always visible but disabled when empty — keeps layout stable.
        self.archived_nav_btn.set_sensitive(False)
        footer_box.append(self.archived_nav_btn)

        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        dashboard_btn = Gtk.Button(label="Dashboard")
        dashboard_btn.set_hexpand(True)
        dashboard_btn.connect("clicked", on_dashboard_clicked)
        dashboard_btn.add_css_class("dashboard-footer-btn")
        buttons_box.append(dashboard_btn)

        graph_btn = Gtk.Button(label="Graph")
        graph_btn.set_hexpand(True)
        graph_btn.connect("clicked", lambda b: on_graph_clicked())
        graph_btn.add_css_class("dashboard-footer-btn")
        buttons_box.append(graph_btn)

        footer_box.append(buttons_box)
        self.append(footer_box)

        # Store for active-state highlighting via set_active_view().
        self._nav_buttons = {"dashboard": dashboard_btn, "graph": graph_btn}

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Delegate to the app's SearchController for debounced search."""
        self.app.search.on_search_changed(entry)

    # ------------------------------------------------------------------ #
    # Archive helpers
    # ------------------------------------------------------------------ #

    def maybe_exit_archive_view(self) -> None:
        """If the archive is now empty and visible, switch back to the main list."""
        if (
            not self.app.cfg.archived
            and self.stack.get_visible_child_name() == "archive"
        ):
            self.stack.set_visible_child_name("main")
            self.archived_nav_btn.set_label("Archived Notes")
        self.archived_nav_btn.set_sensitive(bool(self.app.cfg.archived))

    def set_active_view(self, view: str) -> None:
        """Highlight the footer nav button matching *view* (editor/dashboard/graph)."""
        for btn_name, btn in self._nav_buttons.items():
            if btn_name == view:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def toggle_archive_view(self) -> None:
        """Toggle between the main note list and the archived note list."""
        if self.stack.get_visible_child_name() == "archive":
            self.stack.set_visible_child_name("main")
            self.archived_nav_btn.set_label("Archived Notes")
        else:
            self.stack.set_visible_child_name("archive")
            self.archived_nav_btn.set_label("Back to Notes")

    # ------------------------------------------------------------------ #
    # Sidebar toggle
    # ------------------------------------------------------------------ #

    def on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        """Show or hide the sidebar pane and persist the preference."""
        visible = button.get_active()
        self.app.split_view.set_show_sidebar(visible)
        self.app.cfg.set("show_sidebar", visible)

    # ------------------------------------------------------------------ #
    # Populate
    # ------------------------------------------------------------------ #

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
                self._make_row(note, snippet_fn(note), is_pinned=False,
                               on_right_click=on_right_click, base_dir=base_dir)
            )

        if not pinned_notes and not other_notes:
            msg = "No notes match." if filter_text else "No notes yet."
            self.main_list.append(create_empty_state_widget(msg, base_dir))

        for note in archived_notes:
            fl = filter_text.lower()
            # Check name first (cheap), then content (requires metadata read).
            if filter_text and fl not in note.lower() and fl not in snippet_fn(note).lower():
                continue
            self.archive_list.append(
                self._make_row(note, snippet_fn(note), is_archived=True,
                               on_right_click=on_right_click, base_dir=base_dir)
            )

        self.archived_nav_btn.set_sensitive(bool(archived_notes))

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _clear(self, list_box: Gtk.ListBox) -> None:
        """Remove all children from *list_box*."""
        while (child := list_box.get_first_child()):
            list_box.remove(child)

    def _make_row(
        self,
        note_name: str,
        snippet_text: str,
        is_pinned: bool = False,
        is_archived: bool = False,
        on_right_click: Callable[..., Any] | None = None,
        base_dir: Path | None = None,
    ) -> Gtk.ListBoxRow:
        """Build and return a single sidebar row widget for *note_name*."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        # Title row
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        label = Gtk.Label(label=note_name, xalign=0)
        label.add_css_class("sidebar-label")
        if is_archived:
            label.add_css_class("muted-label")
        label.set_hexpand(True)
        title_box.append(label)

        if is_pinned:
            pin_icon = Gtk.Image()
            pin_icon.set_from_icon_name(_get_pin_icon_name())
            title_box.append(pin_icon)

        box.append(title_box)

        # Snippet
        snippet = Gtk.Label(label=snippet_text, xalign=0)
        snippet.add_css_class("sidebar-snippet")
        snippet.set_ellipsize(Pango.EllipsizeMode.END)
        snippet.set_hexpand(True)
        box.append(snippet)

        row.set_child(box)
        row.note_name = note_name
        row.title_label = label      # stored for O(1) in-place updates
        row.snippet_label = snippet  # stored for O(1) in-place updates

        if on_right_click:
            gesture = Gtk.GestureClick(button=3)
            gesture.connect("pressed", on_right_click, row, is_archived)
            row.add_controller(gesture)

        return row
