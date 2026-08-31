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
    on_media: Callable[[], None] | None = None,
    on_lock: Callable[[], None] | None = None,
    on_new_from_template: Callable[[], None] | None = None,
    on_quick_add: Callable[[], None] | None = None,
    on_speech_toggle: Callable[[], None] | None = None,
    on_find_replace: Callable[[], None] | None = None,
    on_sidebar_search: Callable[[], None] | None = None,
    on_bold: Callable[[], None] | None = None,
    on_italic: Callable[[], None] | None = None,
    on_underline: Callable[[], None] | None = None,
) -> None:
    # F1     help               Ctrl+N  new note          Ctrl+D  dashboard
    # Ctrl+G graph              Ctrl+F  find in editor    Ctrl+H  find & replace
    # Ctrl+Shift+F  search notes
    # Ctrl+L lock notes         Ctrl+Shift+T  timestamp   Ctrl+Shift+Z  zen mode
    # Ctrl+T quick add task     Ctrl+Shift+P  pin note    Ctrl+Shift+A  archive note
    # Ctrl+Shift+S  settings    Ctrl+M  media gallery    Ctrl+Q  quit
    # Escape back / clear       Delete  delete note
    controller = Gtk.ShortcutController()
    controller.set_scope(Gtk.ShortcutScope.GLOBAL)

    bindings: list[tuple[str, Callable]] = [
        ("Delete", on_delete),
        (get_accel("q"), quit_app),
        (get_accel("n"), on_new_note),
        (get_accel("d"), on_dashboard),
        (get_accel("g"), on_graph),
        (get_accel("f"), on_search),
        (get_accel("<Shift>f"), on_sidebar_search or on_search),
        (get_accel("<Shift>t"), on_timestamp),
        (get_accel("<Shift>z"), on_zen_mode),
        ("Escape", on_escape),
    ]
    if on_help:
        bindings.append(("F1", on_help))
    if on_find_replace:
        bindings.append((get_accel("h"), on_find_replace))
    if on_pin:
        bindings.append((get_accel("<Shift>p"), on_pin))
    if on_archive:
        bindings.append((get_accel("<Shift>a"), on_archive))
    if on_settings:
        bindings.append((get_accel("<Shift>s"), on_settings))
    if on_media:
        bindings.append((get_accel("m"), on_media))
    if on_lock:
        bindings.append((get_accel("l"), on_lock))
    if on_new_from_template:
        bindings.append((get_accel("<Shift>n"), on_new_from_template))
    if on_quick_add:
        bindings.append((get_accel("t"), on_quick_add))
    if on_speech_toggle:
        bindings.append(("<Control>space", on_speech_toggle))
    if on_bold:
        bindings.append((get_accel("b"), on_bold))
    if on_italic:
        bindings.append((get_accel("i"), on_italic))
    if on_underline:
        bindings.append((get_accel("u"), on_underline))

    for trigger_str, callback in bindings:
        controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(trigger_str),
                Gtk.CallbackAction.new(lambda *_, cb=callback: cb() or True),
            )
        )

    win.add_controller(controller)
