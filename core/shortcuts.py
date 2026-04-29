import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib

def setup_shortcuts(win, on_new_note, on_dashboard, on_graph, on_search, on_escape, on_delete, quit_app):
    controller = Gtk.ShortcutController()
    controller.set_scope(Gtk.ShortcutScope.GLOBAL)
    
    # Delete
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("Delete"),
        Gtk.CallbackAction.new(lambda *args: on_delete() or True)
    ))
    # Ctrl+Q
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("<Control>q"),
        Gtk.CallbackAction.new(lambda *args: quit_app() or True)
    ))
    # Ctrl+N
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("<Control>n"),
        Gtk.CallbackAction.new(lambda *args: on_new_note() or True)
    ))
    # Ctrl+D
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("<Control>d"),
        Gtk.CallbackAction.new(lambda *args: on_dashboard() or True)
    ))
    # Ctrl+G
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("<Control>g"),
        Gtk.CallbackAction.new(lambda *args: on_graph() or True)
    ))
    # Ctrl+F
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("<Control>f"),
        Gtk.CallbackAction.new(lambda *args: on_search() or True)
    ))
    # Escape
    controller.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("Escape"),
        Gtk.CallbackAction.new(lambda *args: on_escape() or True)
    ))
    win.add_controller(controller)
