"""History popover — shows git commit history and diffs for a note."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk

from core.translations import tr

if TYPE_CHECKING:
    from core.versioning import CommitInfo, GitVersionController


_DIFF_TAG_SPECS: list[tuple[str, str, str]] = [
    ("diff-added", "background", "#2ea04366"),
    ("diff-removed", "background", "#f8514966"),
    ("diff-hunk", "foreground", "#58a6ff"),
]


def _relative_time(dt) -> str:
    now = dt.now(tz=dt.tzinfo)
    delta = now - dt
    if delta.total_seconds() < 60:
        return tr("Just now")
    if delta.total_seconds() < 3600:
        m = int(delta.total_seconds() / 60)
        return tr("{m}m ago").format(m=m)
    if delta.total_seconds() < 86400:
        h = int(delta.total_seconds() / 3600)
        return tr("{h}h ago").format(h=h)
    if delta.days < 7:
        return tr("{d}d ago").format(d=delta.days)
    return dt.strftime("%b %d")


def _commit_type_label(message: str) -> str:
    kind = message.split(":", 1)[0] if ":" in message else message
    return {
        "auto": tr("Auto-save"),
        "snapshot": tr("Snapshot"),
        "delete": tr("Deleted"),
        "rename": tr("Renamed"),
    }.get(kind, message)


def _filter_diff(diff_text: str) -> str:
    """Remove git plumbing lines that have no value for end users."""
    lines = diff_text.split("\n")
    filtered = []
    for line in lines:
        if line.startswith("diff --git "):
            continue
        if line.startswith("index "):
            continue
        if line.startswith("--- a/"):
            continue
        if line.startswith("+++ b/"):
            continue
        filtered.append(line)
    return "\n".join(filtered)


class HistoryPopover(Gtk.Popover):
    """Popover showing commit history and diffs for a note."""

    def __init__(
        self,
        note_name: str,
        git_controller: GitVersionController,
        on_restore: Callable[[str, str], None],
        on_snapshot: Callable[[], None] | None = None,
        text_view: Gtk.Widget | None = None,
        executor: Callable[[Callable], None] | None = None,
    ) -> None:
        super().__init__()
        self._note_name = note_name
        self._git = git_controller
        self._on_restore = on_restore
        self._on_snapshot = on_snapshot
        self._text_view = text_view
        self._executor = executor
        self._commits: list[CommitInfo] = []
        self._selected_commit: str | None = None

        self.set_size_request(480, 420)
        self.set_autohide(True)
        self.connect("closed", self._on_closed)

        self._build_ui()
        self._load_history()

    def _build_ui(self) -> None:
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        main_box.set_margin_start(8)
        main_box.set_margin_end(8)
        main_box.set_margin_top(8)
        main_box.set_margin_bottom(8)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_label = Gtk.Label(
            label=tr("History: {note_name}").format(note_name=self._note_name),
            xalign=0,
        )
        header_label.add_css_class("heading")
        header_label.set_hexpand(True)
        header_box.append(header_label)

        if self._on_snapshot:
            snap_btn = Gtk.Button(label=tr("Snapshot"))
            snap_btn.add_css_class("toolbar-btn")
            snap_btn.connect("clicked", self._on_snapshot_clicked)
            header_box.append(snap_btn)
        main_box.append(header_box)

        self._spinner = Gtk.Spinner()
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_margin_top(12)
        self._spinner.set_margin_bottom(12)
        self._spinner.start()
        main_box.append(self._spinner)

        self._empty_label = Gtk.Label(label=tr("No version history found"))
        self._empty_label.add_css_class("dim-label")
        self._empty_label.set_visible(False)
        self._empty_label.set_margin_top(12)
        self._empty_label.set_margin_bottom(12)
        main_box.append(self._empty_label)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_vexpand(True)
        paned.set_resize_start_child(False)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_position(180)

        self._commit_list = Gtk.ListBox()
        self._commit_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._commit_list.connect("row-selected", self._on_commit_selected)
        scrolled_list = Gtk.ScrolledWindow()
        scrolled_list.set_child(self._commit_list)
        scrolled_list.set_vexpand(False)
        scrolled_list.set_max_content_height(200)
        scrolled_list.set_propagate_natural_height(True)
        paned.set_start_child(scrolled_list)

        diff_frame = Gtk.Frame()
        diff_frame.set_margin_top(4)
        self._diff_view = Gtk.TextView()
        self._diff_view.set_editable(False)
        self._diff_view.set_cursor_visible(False)
        self._diff_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self._diff_view.set_monospace(True)
        self._diff_view.add_css_class("diff-view")
        self._diff_view.set_left_margin(8)
        self._diff_view.set_right_margin(8)
        self._diff_view.set_top_margin(4)
        self._diff_view.set_bottom_margin(4)

        buf = self._diff_view.get_buffer()
        tag_table = buf.get_tag_table()
        for name, prop, val in _DIFF_TAG_SPECS:
            tag = Gtk.TextTag.new(name)
            if prop == "background":
                tag.set_property("background-rgba", _parse_rgba(val))
            else:
                tag.set_property("foreground-rgba", _parse_rgba(val))
            tag_table.add(tag)

        diff_scrolled = Gtk.ScrolledWindow()
        diff_scrolled.set_child(self._diff_view)
        diff_scrolled.set_vexpand(True)
        diff_frame.set_child(diff_scrolled)
        paned.set_end_child(diff_frame)

        main_box.append(paned)

        self._restore_btn = Gtk.Button(label=tr("Restore this version"))
        self._restore_btn.add_css_class("suggested-action")
        self._restore_btn.set_halign(Gtk.Align.END)
        self._restore_btn.set_margin_top(6)
        self._restore_btn.set_sensitive(False)
        self._restore_btn.connect("clicked", self._on_restore_clicked)
        main_box.append(self._restore_btn)

        self.set_child(main_box)

    def _load_history(self) -> None:
        if self._executor:
            self._executor(self._do_load_history)
        else:
            self._do_load_history()

    def _do_load_history(self) -> None:
        self._commits = self._git.history(self._note_name)
        GLib.idle_add(self._display_commits)

    def _display_commits(self) -> bool:
        self._spinner.stop()
        self._spinner.set_visible(False)

        if not self._commits:
            self._empty_label.set_visible(True)
            return False

        for c in self._commits:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_margin_start(4)
            box.set_margin_end(4)
            box.set_margin_top(1)
            box.set_margin_bottom(1)

            label_type = _commit_type_label(c.message)
            type_label = Gtk.Label(label=label_type, xalign=0)
            type_label.add_css_class("monospace")
            type_label.set_size_request(80, -1)
            box.append(type_label)

            time_label = Gtk.Label(
                label=_relative_time(c.timestamp),
                xalign=1,
            )
            time_label.add_css_class("dim-label")
            time_label.set_hexpand(True)
            box.append(time_label)

            row.set_child(box)
            row._hexsha = c.hexsha
            self._commit_list.append(row)

        return False

    def _on_snapshot_clicked(self, _btn: Gtk.Button) -> None:
        if self._on_snapshot:
            self._on_snapshot()
            self._reload_history()

    def _reload_history(self) -> None:
        for child in self._commit_list:
            self._commit_list.remove(child)
        self._commit_list.unselect_all()
        self._selected_commit = None
        self._restore_btn.set_sensitive(False)
        self._diff_view.get_buffer().set_text("")
        self._display_commits()
        self._load_history()

    def _on_commit_selected(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if not row:
            self._restore_btn.set_sensitive(False)
            return
        hexsha = getattr(row, "_hexsha", None)
        if not hexsha:
            return
        self._selected_commit = hexsha
        self._restore_btn.set_sensitive(True)
        self._diff_view.get_buffer().set_text(tr("Loading diff..."))
        if self._executor:
            self._executor(lambda: self._do_load_diff(hexsha))
        else:
            self._do_load_diff(hexsha)

    def _do_load_diff(self, hexsha: str) -> None:
        diff_text = self._git.diff(hexsha, self._note_name)
        if not diff_text:
            is_enc = self._git._note_filename(self._note_name).endswith(".md.enc")
            if is_enc:
                diff_text = tr("(binary file)")
            else:
                content = self._git.restore(hexsha, self._note_name)
                if content is not None:
                    diff_text = tr("(initial version)")
                else:
                    diff_text = tr("(binary file)")
        GLib.idle_add(lambda: self._display_diff(diff_text))

    def _display_diff(self, diff_text: str) -> None:
        filtered = _filter_diff(diff_text)
        buf = self._diff_view.get_buffer()
        buf.set_text(filtered)
        _apply_diff_tags(buf, filtered)

    def _on_restore_clicked(self, _btn: Gtk.Button) -> None:
        if not self._selected_commit:
            return
        hexsha = self._selected_commit

        def _do_restore():
            content = self._git.restore(hexsha, self._note_name)
            if content is not None:
                GLib.idle_add(lambda: self._finish_restore(content))
            else:
                GLib.idle_add(
                    lambda: self._diff_view.get_buffer().set_text(
                        tr("Error: could not restore this version")
                    )
                )

        if self._executor:
            self._executor(_do_restore)
        else:
            _do_restore()

    def _finish_restore(self, content: str | bytes) -> None:
        self._on_restore(self._note_name, content)
        self.popdown()

    def _on_closed(self, popover: HistoryPopover) -> None:
        if self._text_view is not None:
            GLib.idle_add(self._text_view.grab_focus)


def _parse_rgba(hex_color: str) -> Gdk.RGBA:
    """Convert a '#RRGGBBAA' hex string to a GdkRGBA."""
    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    return rgba


def _apply_diff_tags(buf: Gtk.TextBuffer, diff_text: str) -> None:
    """Apply colour tags to a unified-diff buffer based on line prefixes."""
    tag_table = buf.get_tag_table()

    tags = {
        "+": tag_table.lookup("diff-added"),
        "-": tag_table.lookup("diff-removed"),
        "@": tag_table.lookup("diff-hunk"),
    }

    line_iter = buf.get_start_iter()
    while not line_iter.is_end():
        line_start = line_iter.copy()
        if not line_iter.forward_line():
            break

        line_end = line_iter.copy()
        text = buf.get_text(line_start, line_end, False)
        prefix = text[0] if text else ""

        tag = tags.get(prefix)
        if tag:
            buf.apply_tag(tag, line_start, line_end)
