"""Full-page spreadsheet editor for pipe tables."""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from core.table import Table
from core.translations import tr

logger = logging.getLogger(__name__)


class TableView(Gtk.Box):
    """A full-page editable table view that replaces the note in content_stack."""

    def __init__(
        self,
        on_save_and_insert: Callable[[str, Table], None],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._on_save_and_insert = on_save_and_insert
        self._on_close = on_close
        self._table_id: str = ""
        self._table: Table | None = None
        self._column_entries: list[list[Gtk.Entry]] = []
        self._last_focused_cell: tuple[int, int] | None = None
        self._context_cell: tuple[int, int] | None = None
        self._context_popover: Gtk.Popover | None = None

        self._undo_stack: list[Table] = []
        self._redo_stack: list[Table] = []

        self._build_toolbar()
        self._build_table()

    def _build_toolbar(self) -> None:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(4)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)

        back_btn = Gtk.Button(label=tr("Back"))
        back_btn.add_css_class("pill")
        back_btn.connect("clicked", lambda _: self._on_close())
        toolbar.append(back_btn)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        add_row_btn = Gtk.Button(label=tr("Add Row"))
        add_row_btn.add_css_class("pill")
        add_row_btn.connect("clicked", lambda _: self._add_row())
        add_row_btn.set_tooltip_text(tr("Insert a new row"))
        toolbar.append(add_row_btn)

        add_col_btn = Gtk.Button(label=tr("Add Column"))
        add_col_btn.add_css_class("pill")
        add_col_btn.connect("clicked", lambda _: self._add_column())
        add_col_btn.set_tooltip_text(tr("Insert a new column"))
        toolbar.append(add_col_btn)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        undo_btn = Gtk.Button(label=tr("Undo"))
        undo_btn.add_css_class("pill")
        undo_btn.connect("clicked", lambda _: self._undo())
        undo_btn.set_tooltip_text(tr("Undo last action"))
        toolbar.append(undo_btn)

        redo_btn = Gtk.Button(label=tr("Redo"))
        redo_btn.add_css_class("pill")
        redo_btn.connect("clicked", lambda _: self._redo())
        redo_btn.set_tooltip_text(tr("Redo last undone action"))
        toolbar.append(redo_btn)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        save_btn = Gtk.Button(label=tr("Save and Insert"))
        save_btn.add_css_class("pill")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda _: self._on_save_and_insert_clicked())
        save_btn.set_tooltip_text(tr("Save table and insert into note"))
        toolbar.append(save_btn)

        self.append(toolbar)

    def _build_table(self) -> None:
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_hexpand(True)

        self._grid = Gtk.Grid()
        self._grid.set_column_spacing(0)
        self._grid.set_row_spacing(0)
        self._grid.set_margin_top(12)
        self._grid.set_margin_start(12)
        self._grid.set_margin_end(0)
        self._grid.set_margin_bottom(0)
        self._grid.add_css_class("table-editor-grid")

        self._scrolled.set_child(self._grid)
        self.append(self._scrolled)

    def _make_entry(
        self, text: str, placeholder: str, css_class: str, row: int, col: int
    ) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.set_text(text)
        entry.set_placeholder_text(placeholder)
        entry.add_css_class(css_class)
        entry._saved_text = text

        entry.connect("activate", self._on_cell_activated, row, col)

        key_ctl = Gtk.EventControllerKey.new()
        key_ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctl.connect("key-pressed", self._on_cell_tab_pressed, row, col)
        entry.add_controller(key_ctl)

        focus_ctl = Gtk.EventControllerFocus.new()
        focus_ctl.connect("enter", self._on_cell_focus_enter, row, col)
        focus_ctl.connect("leave", self._on_cell_focus_leave, row, col)
        entry.add_controller(focus_ctl)

        rclick = Gtk.GestureClick.new()
        rclick.set_button(3)
        rclick.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        rclick.connect("pressed", self._on_cell_right_click, row, col)
        entry.add_controller(rclick)

        return entry

    def _on_cell_focus_enter(
        self, _ctl: Gtk.EventControllerFocus, row: int, col: int
    ) -> None:
        self._last_focused_cell = (row, col)
        entry = self._column_entries[row][col]
        entry._saved_text = entry.get_text()

    def _on_cell_focus_leave(
        self, _ctl: Gtk.EventControllerFocus, row: int, col: int
    ) -> None:
        if row >= len(self._column_entries) or col >= len(self._column_entries[row]):
            return
        entry = self._column_entries[row][col]
        if entry.get_text() != entry._saved_text:
            self._push_undo()

    def _on_cell_right_click(
        self,
        gesture: Gtk.GestureClick,
        n_press: int,
        x: float,
        y: float,
        row: int,
        col: int,
    ) -> None:
        if n_press != 1:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._context_cell = (row, col)
        entry = self._column_entries[row][col]
        self._show_context_menu(entry, x, y)

    def _show_context_menu(self, entry: Gtk.Entry, x: float, y: float) -> None:
        self._dismiss_context_menu()
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        def item(label: str, callback: Callable[[], None]) -> None:
            btn = Gtk.Button(label=label)
            btn.add_css_class("context-menu-item")
            btn.connect("clicked", lambda _: (self._dismiss_context_menu(), callback()))
            menu_box.append(btn)

        item(tr("Insert Row Above"), self._insert_row_above)
        item(tr("Insert Column Left"), self._insert_column_left)

        menu_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        item(tr("Delete Row"), self._delete_context_row)
        item(tr("Delete Column"), self._delete_context_column)

        popover = Gtk.Popover()
        popover.set_child(menu_box)
        popover.set_parent(entry)
        popover.popup()
        self._context_popover = popover

    def _dismiss_context_menu(self) -> None:
        if self._context_popover is not None:
            self._context_popover.popdown()
            self._context_popover.unparent()
            self._context_popover = None

    def set_table(self, table_id: str, table: Table) -> None:
        """Load a table for editing."""
        self._table_id = table_id
        self._table = table
        self._rebuild_grid()
        self._undo_stack.clear()
        self._redo_stack.clear()

    def focus_first_cell(self) -> None:
        """Grab keyboard focus on the first editable cell."""
        if not self._column_entries:
            return
        if len(self._column_entries) > 1:
            self._column_entries[1][0].grab_focus()
        else:
            self._column_entries[0][0].grab_focus()

    def _rebuild_grid(self) -> None:
        """Clear and rebuild the editable grid from self._table."""
        self._dismiss_context_menu()
        child = self._grid.get_first_child()
        while child:
            self._grid.remove(child)
            child = self._grid.get_first_child()

        self._column_entries = []
        tbl = self._table
        if tbl is None:
            return

        ncols = len(tbl.headers)
        nrows = len(tbl.rows)

        # Header row
        row_entries: list[Gtk.Entry] = []
        for ci in range(ncols):
            entry = self._make_entry(
                tbl.headers[ci] if ci < len(tbl.headers) else "",
                tr("Header"),
                "table-header-entry",
                0,
                ci,
            )
            self._grid.attach(entry, ci, 0, 1, 1)
            row_entries.append(entry)
        self._column_entries.append(row_entries)

        # Data rows
        for ri in range(nrows):
            row_entries = []
            for ci in range(ncols):
                entry = self._make_entry(
                    tbl.rows[ri][ci] if ci < len(tbl.rows[ri]) else "",
                    "",
                    "table-data-entry",
                    ri + 1,
                    ci,
                )
                self._grid.attach(entry, ci, ri + 1, 1, 1)
                row_entries.append(entry)
            self._column_entries.append(row_entries)

    def _on_cell_activated(self, entry: Gtk.Entry, row: int, col: int) -> None:
        """Move focus to cell directly below on Enter — add row on last row."""
        GLib.idle_add(self._do_navigate_down, row, col)

    def _do_navigate_down(self, row: int, col: int) -> None:
        nrows = len(self._column_entries) - 1  # exclude header
        if row < nrows:
            self._column_entries[row + 1][col].grab_focus()
        else:
            self._add_row()
            if self._column_entries:
                self._column_entries[-1][col].grab_focus()

    def _on_cell_tab_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gtk.ModifierType,
        row: int,
        col: int,
    ) -> bool:
        """Intercept Tab at CAPTURE phase — move right, wrap, add row on last cell."""
        if keyval != Gdk.KEY_Tab or (state & Gdk.ModifierType.SHIFT_MASK):
            return False
        GLib.idle_add(self._do_tab_forward, row, col)
        return True

    def _do_tab_forward(self, row: int, col: int) -> None:
        ncols = len(self._table.headers) if self._table else 1
        nrows = len(self._column_entries) - 1
        if col + 1 < ncols:
            self._column_entries[row][col + 1].grab_focus()
        elif row < nrows:
            self._column_entries[row + 1][0].grab_focus()
        else:
            self._add_row()
            if self._column_entries:
                self._column_entries[-1][0].grab_focus()

    # ---- Undo / Redo ----

    def _snapshot_table(self) -> Table:
        tbl = self._collect_table()
        return Table(
            headers=list(tbl.headers),
            rows=[list(r) for r in tbl.rows],
            col_alignments=list(tbl.col_alignments) if tbl.col_alignments else [],
            raw_lines=list(tbl.raw_lines) if tbl.raw_lines else [],
        )

    def _push_undo(self) -> None:
        self._undo_stack.append(self._snapshot_table())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot_table())
        self._table = self._undo_stack.pop()
        self._rebuild_grid()

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot_table())
        self._table = self._redo_stack.pop()
        self._rebuild_grid()

    # ---- Structural operations ----

    def _collect_table(self) -> Table:
        """Read current grid values into a new Table."""
        if not self._column_entries:
            return Table()
        headers = [e.get_text() for e in self._column_entries[0]]
        rows: list[list[str]] = []
        for row_entries in self._column_entries[1:]:
            rows.append([e.get_text() for e in row_entries])
        return Table(headers=headers, rows=rows)

    def _add_row(self) -> None:
        self._push_undo()
        tbl = self._collect_table()
        ncols = len(tbl.headers) if tbl.headers else 1
        tbl.rows.append([""] * ncols)
        self._table = tbl
        self._rebuild_grid()

    def _add_column(self) -> None:
        self._push_undo()
        tbl = self._collect_table()
        tbl.headers.append("")
        for row in tbl.rows:
            row.append("")
        self._table = tbl
        self._rebuild_grid()

    def _delete_row(self) -> None:
        if self._last_focused_cell is None:
            return
        ri = self._last_focused_cell[0]
        if ri == 0:
            return
        self._push_undo()
        tbl = self._collect_table()
        di = ri - 1
        if tbl.rows and di < len(tbl.rows):
            tbl.rows.pop(di)
        self._table = tbl
        self._rebuild_grid()

    def _delete_column(self) -> None:
        if self._last_focused_cell is None:
            return
        ci = self._last_focused_cell[1]
        tbl = self._collect_table()
        ncols = len(tbl.headers)
        if ncols <= 1:
            return
        self._push_undo()
        if ci < len(tbl.headers):
            tbl.headers.pop(ci)
            for row in tbl.rows:
                if ci < len(row):
                    row.pop(ci)
        self._table = tbl
        self._rebuild_grid()

    # ---- Context-menu operations ----

    def _insert_row_above(self) -> None:
        if self._context_cell is None:
            return
        self._push_undo()
        ri = self._context_cell[0]
        tbl = self._collect_table()
        ncols = len(tbl.headers) if tbl.headers else 1
        new_row = [""] * ncols
        if ri == 0:
            tbl.rows.insert(0, new_row)
        else:
            tbl.rows.insert(ri - 1, new_row)
        self._table = tbl
        self._rebuild_grid()

    def _insert_column_left(self) -> None:
        if self._context_cell is None:
            return
        self._push_undo()
        ci = self._context_cell[1]
        tbl = self._collect_table()
        ncols = len(tbl.headers)
        if ci < ncols:
            tbl.headers.insert(ci, "")
            for row in tbl.rows:
                row.insert(ci, "")
        else:
            tbl.headers.insert(0, "")
            for row in tbl.rows:
                row.insert(0, "")
        self._table = tbl
        self._rebuild_grid()

    def _delete_context_row(self) -> None:
        if self._context_cell is None:
            return
        ri = self._context_cell[0]
        if ri == 0:
            return
        self._push_undo()
        tbl = self._collect_table()
        di = ri - 1
        if tbl.rows and di < len(tbl.rows):
            tbl.rows.pop(di)
        self._table = tbl
        self._rebuild_grid()

    def _delete_context_column(self) -> None:
        if self._context_cell is None:
            return
        ci = self._context_cell[1]
        tbl = self._collect_table()
        ncols = len(tbl.headers)
        if ncols <= 1:
            return
        self._push_undo()
        if ci < len(tbl.headers):
            tbl.headers.pop(ci)
            for row in tbl.rows:
                if ci < len(row):
                    row.pop(ci)
        self._table = tbl
        self._rebuild_grid()

    def _on_save_and_insert_clicked(self) -> None:
        tbl = self._collect_table()
        self._on_save_and_insert(self._table_id, tbl)
