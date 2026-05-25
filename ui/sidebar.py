"""Sidebar UI component — note list, search, and navigation footer."""

from __future__ import annotations

import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk, Pango

from core.services import patch_sidebar_row
from core.utils import clear_listbox, create_empty_state_widget

if TYPE_CHECKING:
    from main import TokyoNotes


class Sidebar(Gtk.Box):
    """Vertical sidebar: header, search entry, note lists, and nav footer."""

    def __init__(
        self,
        app: TokyoNotes,
        on_new_note: Callable[..., Any],
        on_new_from_template: Callable[..., Any],
        on_dashboard_clicked: Callable[..., Any],
        on_archive_clicked: Callable[..., Any],
        on_graph_clicked: Callable[..., Any],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.add_css_class("sidebar")

        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label="Tokyo Notes"))

        new_btn = Gtk.MenuButton()
        new_btn.set_tooltip_text("New note")
        new_btn.set_direction(Gtk.ArrowType.DOWN)
        new_btn.add_css_class("sidebar-icon-btn")
        new_btn.add_css_class("flat")
        new_img = Gtk.Image.new_from_file(
            str(self.app.base_dir / "assets" / "sidebar" / "new-note.svg")
        )
        new_img.set_pixel_size(16)
        new_btn.set_child(new_img)

        new_menu = Gio.Menu()
        new_menu.append("New Note", "app.new_note")
        new_menu.append("New Note from template", "app.new_from_template")
        new_btn.set_menu_model(new_menu)

        sidebar_header.pack_start(new_btn)
        self.append(sidebar_header)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search notes…")
        self.search_entry.set_can_focus(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect(
            "stop-search",
            lambda _: (self.app.refresh_list(), self.app.text_view.grab_focus()),
        )
        self.append(self.search_entry)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.main_list = Gtk.ListBox()
        self.stack.add_named(self.main_list, "main")
        self.archive_list = Gtk.ListBox()
        self.stack.add_named(self.archive_list, "archive")
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_child(self.stack)
        self.append(self.scrolled)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)

        self.archived_nav_btn = Gtk.Button(label="Archived Notes")
        self.archived_nav_btn.add_css_class("archived-nav-btn")
        self.archived_nav_btn.connect("clicked", on_archive_clicked)
        self.archived_nav_btn.set_sensitive(bool(app.cfg.archived))
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
            "graph": self._graph_btn,
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
        adj = self.scrolled.get_vadjustment()
        scroll_pos = adj.get_value() if adj else 0.0

        self._clear(self.main_list)
        self._clear(self.archive_list)

        # Compute encrypted set once instead of per-row syscalls
        encrypted_set = self.app.notes_manager.get_encrypted_notes()

        pinned_notes = [n for n in main_notes if n in pinned]
        other_notes = [n for n in main_notes if n not in pinned]

        for note in pinned_notes:
            self.main_list.append(
                self._make_row(
                    note,
                    snippet_fn(note),
                    is_pinned=True,
                    is_encrypted=note in encrypted_set,
                    on_right_click=on_right_click,
                    base_dir=base_dir,
                )
            )
        for note in other_notes:
            self.main_list.append(
                self._make_row(
                    note,
                    snippet_fn(note),
                    is_encrypted=note in encrypted_set,
                    on_right_click=on_right_click,
                    base_dir=base_dir,
                )
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
                self._make_row(
                    note,
                    snippet,
                    is_archived=True,
                    is_encrypted=note in encrypted_set,
                    on_right_click=on_right_click,
                    base_dir=base_dir,
                )
            )

        self.archived_nav_btn.set_sensitive(bool(archived_notes))

        if adj:
            adj.set_value(scroll_pos)

    # In-place row mutations (avoids full rebuild)

    def set_row_encrypted(self, note_name: str, is_encrypted: bool) -> bool:
        """Toggle the encrypted/locked state of a sidebar row in-place.

        Updates the lock icon visibility, CSS classes, and snippet text.
        Returns True if the row was found and updated.
        """
        for lb in (self.main_list, self.archive_list):
            child = lb.get_first_child()
            while child:
                if getattr(child, "note_name", None) == note_name:
                    child.is_encrypted = is_encrypted
                    box = child.get_child()
                    if isinstance(box, Gtk.Box):
                        if is_encrypted:
                            box.add_css_class("private-note-locked")
                        else:
                            box.remove_css_class("private-note-locked")
                    icon = getattr(child, "lock_icon", None)
                    if icon is not None:
                        icon.set_visible(is_encrypted)
                    if is_encrypted and hasattr(child, "snippet_label"):
                        child.snippet_label.set_label("Private note")
                    elif hasattr(child, "snippet_label"):
                        meta = self.app.notes_manager.get_metadata(note_name)
                        child.snippet_label.set_label(meta.get("snippet", ""))
                    return True
                child = child.get_next_sibling()
        return False

    def update_row(self, note_name: str, title: str, snippet: str) -> bool:
        """Update a single sidebar row's title and snippet in-place.

        Returns True if the row was found and updated.
        """
        for lb in (self.main_list, self.archive_list):
            child = lb.get_first_child()
            while child:
                if getattr(child, "note_name", None) == note_name:
                    patch_sidebar_row(child, title=title, snippet=snippet)
                    return True
                child = child.get_next_sibling()
        return False

    def add_row(
        self,
        note_name: str,
        snippet: str,
        is_pinned: bool = False,
        is_archived: bool = False,
        is_encrypted: bool = False,
        on_right_click: Callable[..., Any] | None = None,
        base_dir: Path | None = None,
    ) -> None:
        """Insert a single row into the appropriate list box."""
        row = self._make_row(
            note_name,
            snippet,
            is_pinned=is_pinned,
            is_archived=is_archived,
            is_encrypted=is_encrypted,
            on_right_click=on_right_click,
            base_dir=base_dir,
        )
        target = self.archive_list if is_archived else self.main_list
        target.prepend(row)

    def remove_row(self, note_name: str) -> bool:
        """Remove a single row by note name. Returns True if found."""
        for lb in (self.main_list, self.archive_list):
            child = lb.get_first_child()
            while child:
                if getattr(child, "note_name", None) == note_name:
                    lb.remove(child)
                    return True
                child = child.get_next_sibling()
        return False

    # Internal helpers

    @staticmethod
    def _clear(lb: Gtk.ListBox) -> None:
        clear_listbox(lb)

    def _make_row(
        self,
        note_name: str,
        snippet_text: str,
        is_pinned: bool = False,
        is_archived: bool = False,
        is_encrypted: bool = False,
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

        lock_icon = Gtk.Image.new_from_file(
            str(self.app.base_dir / "assets" / "sidebar" / "lock.svg")
        )
        lock_icon.set_pixel_size(16)
        lock_icon.set_visible(is_encrypted)
        lock_icon.add_css_class("lock-icon")
        lock_icon.add_css_class("sidebar-icon")
        title_box.append(lock_icon)

        pin_icon = Gtk.Image.new_from_file(
            str(self.app.base_dir / "assets" / "sidebar" / "pin.svg")
        )
        pin_icon.set_pixel_size(16)
        pin_icon.set_visible(is_pinned)
        pin_icon.add_css_class("sidebar-icon")
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
        row.is_encrypted = is_encrypted
        row.lock_icon = lock_icon

        if is_encrypted:
            box.add_css_class("private-note-locked")

        _app_ref = weakref.ref(self.app)
        hover = Gtk.EventControllerMotion()

        def _on_hover_enter(*_):
            app = _app_ref()
            if app is not None and not (is_encrypted and app._is_session_locked):
                app.notes_manager.read_plain(note_name)

        hover.connect("enter", _on_hover_enter)
        row.add_controller(hover)

        if on_right_click:
            gesture = Gtk.GestureClick(button=3)
            gesture.connect("pressed", on_right_click, row, is_archived)
            row.add_controller(gesture)

        return row

    def update_encrypted_row(self, row: Gtk.ListBoxRow, locked: bool) -> None:
        """Update CSS classes and snippet for an encrypted row on lock state change."""
        if not getattr(row, "is_encrypted", False):
            return
        box = row.get_child()
        if not isinstance(box, Gtk.Box):
            return

        if locked:
            box.add_css_class("private-note-locked")
            box.remove_css_class("private-note-unlocked")
            if hasattr(row, "snippet_label"):
                row.snippet_label.set_label("Private note")
        else:
            box.add_css_class("private-note-unlocked")
            box.remove_css_class("private-note-locked")
            if hasattr(row, "snippet_label"):
                meta = self.app.notes_manager.get_metadata(row.note_name)
                row.snippet_label.set_label(meta.get("snippet", ""))
