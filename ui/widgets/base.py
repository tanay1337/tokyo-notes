from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class WidgetBase(Gtk.Overlay):
    widget_type: str = ""
    widget_title: str = ""

    def __init__(
        self,
        widget_id: str,
        settings: dict[str, Any] | None = None,
        app: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.widget_id = widget_id
        self.settings = settings or {}
        self.app = app

        self.add_css_class("widget-card")

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._content.add_css_class("widget-content")
        self.set_child(self._content)

    def get_config_widget(self) -> Gtk.Widget | None:
        return None

    def apply_config(self) -> None:
        pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.widget_type,
            "id": self.widget_id,
            "settings": dict(self.settings),
        }

    def update_periodic(self) -> None:
        pass
