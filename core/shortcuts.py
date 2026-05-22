"""Global keyboard shortcut registration."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from core.utils import get_accel


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
    quit_app: Callable[[], None],
    on_help: Callable[[], None] | None = None,
    on_pin: Callable[[], None] | None = None,
    on_archive: Callable[[], None] | None = None,
    on_settings: Callable[[], None] | None = None,
    on_lock: Callable[[], None] | None = None,
    on_new_from_template: Callable[[], None] | None = None,
) -> None:
    # Ctrl+N  new note          Ctrl+D  dashboard         Ctrl+G  graph
    # Ctrl+F  search (×2=clear) Ctrl+Q  quit              Ctrl+H  help
    # Ctrl+L  lock notes        Ctrl+Shift+T  timestamp   Ctrl+Shift+Z  zen mode
    # Ctrl+Shift+P  pin note    Ctrl+Shift+A  archive note
    # Ctrl+Shift+S  settings
    # Escape  back / clear      Delete  delete note
    controller = Gtk.ShortcutController()
    controller.set_scope(Gtk.ShortcutScope.GLOBAL)

    bindings: list[tuple[str, Callable]] = [
        ("Delete",              on_delete),
        (get_accel("q"),        quit_app),
        (get_accel("n"),        on_new_note),
        (get_accel("d"),        on_dashboard),
        (get_accel("g"),        on_graph),
        (get_accel("f"),        on_search),
        (get_accel("<Shift>t"), on_timestamp),
        (get_accel("<Shift>z"), on_zen_mode),
        ("Escape",              on_escape),
    ]
    if on_help:
        bindings.append((get_accel("h"), on_help))
    if on_pin:
        bindings.append((get_accel("<Shift>p"), on_pin))
    if on_archive:
        bindings.append((get_accel("<Shift>a"), on_archive))
    if on_settings:
        bindings.append((get_accel("<Shift>s"), on_settings))
    if on_lock:
        bindings.append((get_accel("l"), on_lock))
    if on_new_from_template:
        bindings.append((get_accel("<Shift>n"), on_new_from_template))

    for trigger_str, callback in bindings:
        controller.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string(trigger_str),
            Gtk.CallbackAction.new(lambda *_, cb=callback: cb() or True),
        ))

    win.add_controller(controller)
