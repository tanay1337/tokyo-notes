"""Sidebar UI component — note list, search, and navigation footer."""

from __future__ import annotations

import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, Pango

from core.services import patch_sidebar_row
from core.translations import tr
from core.utils import clear_listbox, create_empty_state_widget, split_note_path

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
        on_flashcard_clicked: Callable[..., Any],
        on_settings_clicked: Callable[..., Any],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.add_css_class("sidebar")

        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label=tr("Tokyo Notes")))

        new_btn = Gtk.MenuButton()
        new_btn.set_tooltip_text(tr("New note"))
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
        new_menu.append("New Folder", "app.new_folder")
        new_btn.set_menu_model(new_menu)

        sidebar_header.pack_start(new_btn)
        self.append(sidebar_header)

        self.search_entry = Gtk.SearchEntry(placeholder_text=tr("Search notes"))
        self.search_entry.set_can_focus(True)
        self.search_entry.set_margin_top(10)
        self.search_entry.set_margin_bottom(10)
        self.search_entry.set_margin_start(10)
        self.search_entry.set_margin_end(10)

        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect(
            "stop-search",
            lambda _: (self.app.refresh_list(), self.app._focus_text_view()),
        )
        self.append(self.search_entry)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.main_list = Gtk.ListBox()
        self.stack.add_named(self.main_list, "main")
        self.archive_list = Gtk.ListBox()
        self.stack.add_named(self.archive_list, "archive")
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_child(self.stack)
        self.append(self.scrolled)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)

        self.archived_nav_btn = Gtk.Button(label=tr("Archived Notes"))
        self.archived_nav_btn.add_css_class("archived-nav-btn")
        self.archived_nav_btn.connect("clicked", on_archive_clicked)
        self.archived_nav_btn.set_sensitive(bool(app.cfg.archived))
        footer.append(self.archived_nav_btn)

        btn_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=4
        )

        def _make_nav_icon_btn(svg_name: str, tooltip: str, callback) -> Gtk.Button:
            btn = Gtk.Button(tooltip_text=tooltip)
            img = Gtk.Image.new_from_file(
                str(self.app.base_dir / "assets" / "sidebar" / svg_name)
            )
            img.set_pixel_size(16)
            btn.set_child(img)
            btn.add_css_class("sidebar-icon-btn")
            btn.add_css_class("dashboard-footer-btn")
            btn.connect("clicked", lambda _: callback())
            btn_row.append(btn)
            return btn

        self._dashboard_btn = _make_nav_icon_btn(
            "dashboard.svg", tr("Dashboard"), on_dashboard_clicked
        )
        self._graph_btn = _make_nav_icon_btn(
            "graph.svg", tr("Knowledge graph"), on_graph_clicked
        )
        self._flashcard_btn = _make_nav_icon_btn(
            "flashcard.svg", tr("Flashcards"), on_flashcard_clicked
        )
        self._settings_btn = _make_nav_icon_btn(
            "settings.svg", tr("Settings"), on_settings_clicked
        )

        footer.append(btn_row)
        self.append(footer)

        self._nav_buttons = {
            "dashboard": self._dashboard_btn,
            "graph": self._graph_btn,
            "flashcard": self._flashcard_btn,
            "settings": self._settings_btn,
        }

        # Right-click on empty listbox area → New Folder
        self._active_popover = None

        # Track folder expand/collapse state across refresh_list calls
        self._folder_expanded: dict[str, bool] = {}
        self._saved_expanded_state: dict[str, bool] = {}
        self._filter_was_active: bool = False

        self._empty_click = Gtk.GestureClick(button=3)
        self._empty_click.connect("pressed", self._on_empty_space_right_click)
        self.main_list.add_controller(self._empty_click)

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
            self.archived_nav_btn.set_label(tr("Archived Notes"))
        self.archived_nav_btn.set_sensitive(bool(self.app.cfg.archived))

    def toggle_archive_view(self) -> None:
        if self.stack.get_visible_child_name() == "archive":
            self.stack.set_visible_child_name("main")
            self.archived_nav_btn.set_label(tr("Archived Notes"))
        else:
            self.stack.set_visible_child_name("archive")
            self.archived_nav_btn.set_label(tr("Back to Notes"))

    def on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        visible = button.get_active()
        self.app.split_view.set_show_sidebar(visible)
        self.app.cfg.set("show_sidebar", visible)

    def _on_empty_space_right_click(
        self,
        gesture: Gtk.GestureClick,
        n_press: int,
        x: float,
        y: float,
    ) -> None:
        """Show 'New Folder…' context menu when right-clicking empty listbox space."""
        if self.main_list.get_row_at_y(int(y)) is not None:
            return  # a note/folder row handled it
        menu = Gio.Menu()
        menu.append("New Folder", "app.new_folder")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        # Parent to the sidebar (never cleared) so _clear(main_list) doesn't break it
        popover.set_parent(self)
        rect = Gdk.Rectangle()
        translated = self.main_list.translate_coordinates(self, int(x), int(y))
        if translated is not None:
            px, py = translated
        else:
            px, py = int(x), int(y)
        rect.x = px
        rect.y = py
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda *_: setattr(self, "_active_popover", None))
        self._active_popover = popover
        popover.popup()

    # Populate

    @staticmethod
    def _get_parent(fp: str) -> str:
        idx = fp.rfind("/")
        return "" if idx == -1 else fp[:idx]

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
        """Rebuild both list boxes from scratch.

        Notes are grouped by folder in the sidebar.  Folders are rendered
        as a tree: subfolders are nested under their parent folder header.
        """
        # Dismiss any active popover (e.g. New Folder menu) before clearing
        if self._active_popover:
            self._active_popover.popdown()
            self._active_popover = None

        adj = self.scrolled.get_vadjustment()
        scroll_pos = adj.get_value() if adj else 0.0

        self._clear(self.main_list)
        self._clear(self.archive_list)

        # Compute encrypted set once instead of per-row syscalls
        encrypted_set = self.app.notes_manager.get_encrypted_notes()
        show_empty = not filter_text

        pinned_notes = [n for n in main_notes if n in pinned]
        other_notes = [n for n in main_notes if n not in pinned]

        # Pinned notes at top (full qualified name shown)
        for note in pinned_notes:
            folder, stem = split_note_path(note)
            self.main_list.append(
                self._make_row(
                    note,
                    snippet_fn(note),
                    display_name=stem,
                    folder_path=folder,
                    is_pinned=True,
                    is_encrypted=note in encrypted_set,
                    on_right_click=on_right_click,
                    base_dir=base_dir,
                )
            )

        # Group unfiled (no folder) and filed notes
        folder_groups: dict[str | None, list[str]] = {}
        for note in other_notes:
            folder, _stem = split_note_path(note)
            folder_groups.setdefault(folder, []).append(note)

        # Save expanded state when search starts
        if filter_text and not self._filter_was_active:
            self._saved_expanded_state = self._folder_expanded.copy()
        # Restore expanded state when search ends
        if not filter_text and self._filter_was_active:
            self._folder_expanded = self._saved_expanded_state.copy()
        self._filter_was_active = bool(filter_text)

        # During search, auto-expand all folders containing matches
        if filter_text:
            for fp in folder_groups:
                if fp is not None:
                    parts = fp.split("/")
                    for i in range(1, len(parts) + 1):
                        self._folder_expanded["/".join(parts[:i])] = True

        # ── Build folder tree from all known folders ──
        configured_order = self.app.cfg.folder_order
        all_folder_paths: set[str] = set()

        # 1. Folders that have notes
        for fp in folder_groups:
            if fp is not None:
                parts = fp.split("/")
                all_folder_paths.add(fp)
                for i in range(1, len(parts)):
                    all_folder_paths.add("/".join(parts[:i]))

        # 2. Empty folders on disk (if show_empty)
        if show_empty:
            disk_folders = set(self.app.notes_manager.get_folders())
            for fp in disk_folders:
                parts = fp.split("/")
                all_folder_paths.add(fp)
                for i in range(1, len(parts)):
                    all_folder_paths.add("/".join(parts[:i]))

        # Build parent→children map
        folder_children: dict[str, list[str]] = {}
        for fp in all_folder_paths:
            parent = self._get_parent(fp)
            folder_children.setdefault(parent, []).append(fp)

        # Pre-compute recursive note counts (deepest first)
        total_counts: dict[str, int] = {}
        for fp in sorted(all_folder_paths, key=lambda p: p.count("/"), reverse=True):
            total_counts[fp] = len(folder_groups.get(fp, [])) + sum(
                total_counts.get(c, 0) for c in folder_children.get(fp, [])
            )

        def _child_sort_key(fp: str) -> tuple:
            if configured_order and fp in configured_order:
                return (0, configured_order.index(fp))
            return (1, fp)

        for parent in folder_children:
            folder_children[parent].sort(key=_child_sort_key)

        # ── Recursive tree renderer ──
        # Renders a single folder (its header + notes + subfolders).
        # Descendants are only added to the listbox if the folder is expanded.
        # Returns the folder-header row (with _descendant_rows populated).
        def _render_folder(
            fp: str, indent: int, add_to_listbox: bool = True
        ) -> Gtk.ListBoxRow:
            direct_notes = folder_groups.get(fp, [])
            note_count = total_counts.get(fp, 0)
            has_enc = any(n in encrypted_set for n in direct_notes)
            is_pin = self.app.cfg.is_folder_pinned(fp)

            f_row = self._make_folder_row(
                fp,
                note_count,
                on_right_click=on_right_click,
                is_pinned=is_pin,
                has_encrypted=has_enc,
                indent_level=indent,
            )
            if add_to_listbox:
                self.main_list.append(f_row)

            show_children = f_row._expanded and add_to_listbox
            descendants: list[Gtk.ListBoxRow] = []

            # Notes directly in this folder
            for note in direct_notes:
                _f, stem = split_note_path(note)
                n_row = self._make_row(
                    note,
                    snippet_fn(note),
                    display_name=stem,
                    is_encrypted=note in encrypted_set,
                    on_right_click=on_right_click,
                    base_dir=base_dir,
                    indent_level=indent + 1,
                )
                descendants.append(n_row)
                if show_children:
                    self.main_list.append(n_row)

            # Subfolders (recursive)
            for child_fp in folder_children.get(fp, []):
                sub_row = _render_folder(
                    child_fp, indent + 1, add_to_listbox=show_children
                )
                descendants.append(sub_row)
                if show_children:
                    descendants.extend(sub_row._descendant_rows)

            f_row._descendant_rows = descendants
            return f_row

        # Determine root-level folders
        root_folders = folder_children.get("", [])
        pinned_root_folders = [
            f for f in root_folders if self.app.cfg.is_folder_pinned(f)
        ]
        unpinned_root_folders = [
            f for f in root_folders if not self.app.cfg.is_folder_pinned(f)
        ]

        # Pinned root folders (with entire subtree) — immediately after pinned notes
        for fp in pinned_root_folders:
            _render_folder(fp, 0)

        # Unfiled notes (no folder, no header)
        unfiled = folder_groups.get(None, [])
        for note in unfiled:
            self.main_list.append(
                self._make_row(
                    note,
                    snippet_fn(note),
                    display_name=note,
                    is_encrypted=note in encrypted_set,
                    on_right_click=on_right_click,
                    base_dir=base_dir,
                )
            )

        # Remaining root folders (unpinned)
        for fp in unpinned_root_folders:
            _render_folder(fp, 0)

        if not pinned_notes and not other_notes:
            msg = tr("No notes match.") if filter_text else tr("No notes yet.")
            self.main_list.append(create_empty_state_widget(msg, base_dir))

        for note in archived_notes:
            if filter_text:
                fl = filter_text.lower()
                snippet = snippet_fn(note)
                if fl not in note.lower() and fl not in snippet.lower():
                    continue
            else:
                snippet = snippet_fn(note)
            _folder, stem = split_note_path(note)
            self.archive_list.append(
                self._make_row(
                    note,
                    snippet,
                    display_name=stem,
                    folder_path=_folder,
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
                if getattr(child, "note_name", None) == note_name and not getattr(
                    child, "_is_folder", False
                ):
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
                        child.snippet_label.set_label(tr("Private note"))
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
                nm = getattr(child, "note_name", None)
                if nm == note_name and not getattr(child, "_is_folder", False):
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
        folder, stem = split_note_path(note_name)
        row = self._make_row(
            note_name,
            snippet,
            display_name=stem,
            folder_path=folder,
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
                nm = getattr(child, "note_name", None)
                if nm == note_name and not getattr(child, "_is_folder", False):
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
        display_name: str | None = None,
        folder_path: str | None = None,
        is_pinned: bool = False,
        is_archived: bool = False,
        is_encrypted: bool = False,
        on_right_click: Callable[..., Any] | None = None,
        base_dir: Path | None = None,
        indent_level: int = 0,
    ) -> Gtk.ListBoxRow:
        """Build a single sidebar row. Attach hover-preload for instant switching.

        *display_name* — label shown in the sidebar (defaults to *note_name*).
        *folder_path* — dimmed secondary label shown beneath the title.
        *indent_level* — nesting depth for tree margin.
        """
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(3)
        box.set_margin_bottom(3)
        if not is_pinned:
            if indent_level:
                box.set_margin_start(indent_level * 16)
            elif folder_path:
                box.set_margin_start(16)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        label = Gtk.Label(label=display_name or note_name, xalign=0)
        label.add_css_class("sidebar-label")
        if is_archived:
            label.add_css_class("muted-label")
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
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

        # Folder path secondary label (dimmed, below title)
        if folder_path:
            path_label = Gtk.Label(label=folder_path, xalign=0)
            path_label.add_css_class("sidebar-snippet")
            path_label.set_opacity(0.6)
            path_label.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(path_label)

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

    def _make_folder_row(
        self,
        folder_path: str,
        note_count: int,
        on_right_click: Callable[..., Any] | None = None,
        is_pinned: bool = False,
        has_encrypted: bool = False,
        indent_level: int = 0,
    ) -> Gtk.ListBoxRow:
        """Build a collapsible folder header row."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.add_css_class("folder-header")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        if indent_level:
            box.set_margin_start(indent_level * 16)

        # Expand/collapse arrow (starts collapsed)
        arrow = Gtk.Image.new_from_file(
            str(
                self.app.base_dir / "assets" / "sidebar" / "folder-toggle-collapsed.svg"
            )
        )
        arrow.set_pixel_size(16)
        arrow.add_css_class("sidebar-icon")
        box.append(arrow)

        # Folder display name (last component) with full path as tooltip
        parts = folder_path.split("/")
        display = parts[-1]
        label = Gtk.Label(label=display, xalign=0)
        label.add_css_class("sidebar-label")
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_tooltip_text(folder_path)
        box.append(label)

        # Pin icon
        pin_icon = Gtk.Image.new_from_file(
            str(self.app.base_dir / "assets" / "sidebar" / "pin.svg")
        )
        pin_icon.set_pixel_size(12)
        pin_icon.set_visible(is_pinned)
        pin_icon.add_css_class("sidebar-icon")
        box.append(pin_icon)

        # Lock icon (folder has encrypted notes)
        lock_icon = Gtk.Image.new_from_file(
            str(self.app.base_dir / "assets" / "sidebar" / "lock.svg")
        )
        lock_icon.set_pixel_size(12)
        lock_icon.set_visible(has_encrypted)
        lock_icon.add_css_class("lock-icon")
        lock_icon.add_css_class("sidebar-icon")
        box.append(lock_icon)

        # Note count badge
        count_label = Gtk.Label(label=str(note_count), xalign=1)
        count_label.add_css_class("muted-label")
        count_label.set_opacity(0.6)
        box.append(count_label)

        row.set_child(box)
        row._is_folder = True
        row.folder_path = folder_path
        row._arrow = arrow
        is_expanded = self._folder_expanded.get(folder_path, False)
        row._expanded = is_expanded
        if is_expanded:
            arrow.set_from_file(
                str(
                    self.app.base_dir
                    / "assets"
                    / "sidebar"
                    / "folder-toggle-expanded.svg"
                )
            )
        row._descendant_rows: list[Gtk.ListBoxRow] = []
        row._count_label = count_label

        # Click to expand/collapse
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_folder_header_clicked, row)
        row.add_controller(click)

        if on_right_click:
            gesture = Gtk.GestureClick(button=3)
            gesture.connect("pressed", on_right_click, row, False)
            row.add_controller(gesture)

        return row

    def _on_folder_header_clicked(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        row: Gtk.ListBoxRow,
    ) -> None:
        """Toggle folder expand/collapse on click."""
        listbox = row.get_parent()
        if not isinstance(listbox, Gtk.ListBox):
            return

        row._expanded = not row._expanded
        self._folder_expanded[
            row._folder_path if hasattr(row, "_folder_path") else row.folder_path
        ] = row._expanded

        icon_name = (
            "folder-toggle-expanded.svg"
            if row._expanded
            else "folder-toggle-collapsed.svg"
        )
        row._arrow.set_from_file(
            str(self.app.base_dir / "assets" / "sidebar" / icon_name)
        )

        if row._expanded:
            position = 0
            child = listbox.get_first_child()
            while child and child != row:
                position += 1
                child = child.get_next_sibling()
            for i, child_row in enumerate(row._descendant_rows):
                if child_row.get_parent() is not listbox:
                    listbox.insert(child_row, position + 1 + i)
            # Re-collapse any nested folders that are still collapsed
            for child_row in row._descendant_rows:
                if getattr(child_row, "_is_folder", False) and not child_row._expanded:
                    for desc in child_row._descendant_rows:
                        if desc.get_parent() is listbox:
                            listbox.remove(desc)
        else:
            for child_row in row._descendant_rows:
                if child_row.get_parent() is listbox:
                    listbox.remove(child_row)

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
                row.snippet_label.set_label(tr("Private note"))
        else:
            box.add_css_class("private-note-unlocked")
            box.remove_css_class("private-note-locked")
            if hasattr(row, "snippet_label"):
                meta = self.app.notes_manager.get_metadata(row.note_name)
                row.snippet_label.set_label(meta.get("snippet", ""))
