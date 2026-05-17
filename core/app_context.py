"""Application state and dependency context.

Gradually replaces ``self.app`` (God-Object) with an explicit typed
context so each subsystem declares only what it needs.
"""
from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class AppState:
    """Centralised mutable runtime state."""

    current_note: str | None = None
    is_loading: bool = False
    last_cursor_line: int = -1
    has_images: bool = False
    in_zen_mode: bool = False

    # Debounce / timeout handles
    highlight_timeout_id: int = 0
    rename_timeout_id: int = 0
    sidebar_update_timeout_id: int = 0
    image_timeout_id: int = 0
    search_timeout_id: int = 0


@dataclasses.dataclass
class AppWidgets:
    """References to major widgets created in main.py."""

    win: Any = None
    split_view: Any = None
    content_stack: Any = None
    sidebar: Any = None
    editor: Any = None
    sidebar_toggle: Any = None
    content_title: Any = None
    pdf_btn: Any = None
    back_btn: Any = None
    highlighter: Any = None
    dashboard_view: Any = None
    graph_manager: Any = None
    graph_view: Any = None
    settings_view: Any = None
    dashboard_list: Any = None
    text_view: Any = None
    buffer: Any = None
    toolbar: Any = None
    changed_handler_id: int = 0
    sidebar_toggle_handler: int = 0
    sakura_overlay: Any = None


class AppContext:
    """Typed dependency context shared by all subsystems.

    * ``state``   – mutable runtime data (current note, timeout ids, …)
    * ``widgets`` – widget references populated once during ``do_activate``
    * ``notes_manager``, ``cfg`` – the two service instances
    * Any attribute `` *.ANY`` is forwarded back to the owner application
      (kept for attributes that are not yet extracted).
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self.state = AppState()
        self.widgets = AppWidgets()

        # Will be set during main.py construction before subsystems start.
        self.notes_manager: Any = None
        self.cfg: Any = None
        self.theme_manager: Any = None

    # Transparent passthrough for everything still on main.py

    def __getattr__(self, name: str) -> Any:
        try:
            return getattr(self.state, name)
        except AttributeError:
            pass
        try:
            return getattr(self.widgets, name)
        except AttributeError:
            pass
        return getattr(self._app, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_app", "state", "widgets", "notes_manager", "cfg", "theme_manager"):
            super().__setattr__(name, value)
            return
        # Store on state or widgets if the field exists there, else on app.
        try:
            state = object.__getattribute__(self, "state")
            if name in state.__dict__:
                object.__setattr__(state, name, value)
                return
        except AttributeError:
            pass
        try:
            widgets = object.__getattribute__(self, "widgets")
            if name in widgets.__dict__:
                object.__setattr__(widgets, name, value)
                return
        except AttributeError:
            pass
        object.__getattribute__(self, "_app").__setattr__(name, value)
