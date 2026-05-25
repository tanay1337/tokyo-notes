"""Theme and CSS management for Tokyo Notes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

if TYPE_CHECKING:
    from main import TokyoNotes


class ThemeManager:
    """Manages CSS providers and theme switching."""

    def __init__(self, app: TokyoNotes) -> None:
        self.app = app
        self.theme_provider = Gtk.CssProvider()
        self.style_provider = Gtk.CssProvider()

    def setup_providers(self) -> None:
        """Register CSS providers with GTK.

        The theme provider gets a slightly higher priority than the base
        style provider, allowing themes to override the default rules in
        style.css.
        """
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self.theme_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )
            Gtk.StyleContext.add_provider_for_display(
                display, self.style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def apply_theme(self, theme_name: str) -> None:
        """Apply theme CSS and update highlighter colors.

        CSS class changes on the window are deferred to the caller (main.py)
        rather than guarded with hasattr — apply_theme is only called after
        the window exists.
        """
        theme_path = self.app.base_dir / "themes" / f"{theme_name}.css"
        if theme_path.exists():
            self.theme_provider.load_from_path(str(theme_path))
            style_path = self.app.base_dir / "style.css"
            if style_path.exists():
                self.style_provider.load_from_path(str(style_path))

        if self.app.highlighter:
            self.app.highlighter.update_theme(theme_name)

        style_manager = Adw.StyleManager.get_default()
        if "light" in theme_name:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
