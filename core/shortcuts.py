"""Keyboard shortcut management for Tokyo Notes."""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk

from core.utils import get_accel

if TYPE_CHECKING:
    pass

def setup_shortcuts(
    win: Gtk.ApplicationWindow,
    on_new_note: Callable[[], None],
    on_dashboard: Callable[[], None],
    on_graph: Callable[[], None],
    on_search: Callable[[], None],
    on_escape: Callable[[], None],
    on_delete: Callable[[], None],
    on_timestamp: Callable[[], None],
    on_zen_mode: Callable[[], None],
    quit_app: Callable[[], None]
) -> None:
    """Sets up global keyboard shortcuts for the main application window.
    
    Args:
        win: Main application window.
        on_new_note: Callback for Ctrl+N (New Note).
        on_dashboard: Callback for Ctrl+D (Dashboard).
        on_graph: Callback for Ctrl+G (Graph View).
        on_search: Callback for Ctrl+F (Search).
        on_escape: Callback for Escape (Dismiss/Back).
        on_delete: Callback for Delete (Delete Note).
        on_timestamp: Callback for Shift+T (Insert Timestamp).
        on_zen_mode: Callback for Shift+Z (Zen Mode).
        quit_app: Callback for Ctrl+Q (Quit).
    """
    controller = Gtk.ShortcutController()
    controller.set_scope(Gtk.ShortcutScope.GLOBAL)

    # Delete
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("Delete"),
        Gtk.CallbackAction.new(lambda *args: on_delete() or True)
    ))
    # Quit
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("q")),
        Gtk.CallbackAction.new(lambda *args: quit_app() or True)
    ))
    # New Note
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("n")),
        Gtk.CallbackAction.new(lambda *args: on_new_note() or True)
    ))
    # Toggle Dashboard
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("d")),
        Gtk.CallbackAction.new(lambda *args: on_dashboard() or True)
    ))
    # Toggle Graph
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("g")),
        Gtk.CallbackAction.new(lambda *args: on_graph() or True)
    ))
    # Focus Search
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("f")),
        Gtk.CallbackAction.new(lambda *args: on_search() or True)
    ))
    # Insert Timestamp
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("<Shift>t")),
        Gtk.CallbackAction.new(lambda *args: on_timestamp() or True)
    ))
    # Zen Mode
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string(get_accel("<Shift>z")),
        Gtk.CallbackAction.new(lambda *args: on_zen_mode() or True)
    ))
    # Escape
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("Escape"),
        Gtk.CallbackAction.new(lambda *args: on_escape() or True)
    ))
    win.add_controller(controller)

