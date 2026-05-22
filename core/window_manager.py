"""Window creation and responsive breakpoint management."""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

if TYPE_CHECKING:
    from main import TokyoNotes


class WindowManager:
    """Manages window creation and responsive breakpoints."""

    def __init__(self, app: "TokyoNotes") -> None:
        self.app = app

    def create_window(self) -> Adw.ApplicationWindow:
        """Create and configure the main application window."""
        win = Adw.ApplicationWindow(application=self.app)
        win.set_title("Tokyo Notes")
        win.set_default_size(1000, 700)

        display = Gdk.Display.get_default()
        if display:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            icon_theme.add_search_path(str(self.app.base_dir / "assets"))
            win.set_icon_name("tokyo_notes_icon")

        return win

    def setup_breakpoint(self) -> None:
        """Register a responsive breakpoint that collapses the sidebar on narrow screens."""
        display = Gdk.Display.get_default()
        if not display:
            return

        monitors = display.get_monitors()
        if monitors.get_n_items() == 0:
            return

        # Use the smallest monitor width so the breakpoint works on all screens
        min_width = min(
            monitors.get_item(i).get_geometry().width
            for i in range(monitors.get_n_items())
        )
        half_width = min_width // 2
        condition = Adw.BreakpointCondition.parse(f"max-width: {half_width}px")
        bp = Adw.Breakpoint.new(condition)
        bp.add_setter(self.app.split_view, "collapsed", True)
        bp.add_setter(self.app.sidebar_toggle, "active", False)
        self.app.win.add_breakpoint(bp)
