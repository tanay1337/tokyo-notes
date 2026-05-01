import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib
from core.utils import get_accel

def setup_shortcuts(win, on_new_note, on_dashboard, on_graph, on_search, on_escape, on_delete, on_timestamp, quit_app):
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
    # Escape
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("Escape"),
        Gtk.CallbackAction.new(lambda *args: on_escape() or True)
    ))
    win.add_controller(controller)

