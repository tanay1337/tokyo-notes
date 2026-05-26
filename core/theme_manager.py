"""Theme and CSS management for Tokyo Notes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

if TYPE_CHECKING:
    from main import TokyoNotes

_SYNTAX_RE = re.compile(r"@define-color\s+syntax_(\w+)\s+(#[0-9a-fA-F]+)\s*;")

THEMES: list[dict[str, str]] = [
    {
        "id": "tokyo-light",
        "name": "Tokyo Light",
        "preview": "Clean and bright, inspired by Tokyo Day",
        "type": "light",
    },
    {
        "id": "tokyo-night",
        "name": "Tokyo Night",
        "preview": "Deep blues and vibrant accents",
        "type": "dark",
    },
    {
        "id": "cyberpunk-2077",
        "name": "Cyberpunk 2077",
        "preview": "Night City vibes: Yellow, Cyan, and Black",
        "type": "dark",
    },
    {
        "id": "nord",
        "name": "Nord",
        "preview": "Arctic blue, clean and elegant",
        "type": "dark",
    },
    {
        "id": "gruvbox",
        "name": "Gruvbox",
        "preview": "Retro warm tones, easy on the eyes",
        "type": "dark",
    },
    {
        "id": "dracula",
        "name": "Dracula",
        "preview": "High contrast, vibrant purple tones",
        "type": "dark",
    },
    {
        "id": "catppuccin-mocha",
        "name": "Catppuccin Mocha",
        "preview": "Soothing pastel, warm dark tones",
        "type": "dark",
    },
    {
        "id": "catppuccin-latte",
        "name": "Catppuccin Latte",
        "preview": "Soothing pastel, warm light tones",
        "type": "light",
    },
    {
        "id": "one-dark",
        "name": "One Dark",
        "preview": "Atom's iconic dark theme, clean and modern",
        "type": "dark",
    },
    {
        "id": "monokai",
        "name": "Monokai",
        "preview": "Classic vibrant, high-contrast dark theme",
        "type": "dark",
    },
    {
        "id": "ayu-mirage",
        "name": "Ayu Mirage",
        "preview": "Soft blue-gray, warm accent tones",
        "type": "dark",
    },
    {
        "id": "atom-one-light",
        "name": "Atom One Light",
        "preview": "Clean and bright, Atom's iconic light theme",
        "type": "light",
    },
    {
        "id": "github-dark",
        "name": "GitHub Dark",
        "preview": "GitHub's official dark mode, clean and functional",
        "type": "dark",
    },
    {
        "id": "github-light",
        "name": "GitHub Light",
        "preview": "GitHub's official light theme, clean and airy",
        "type": "light",
    },
    {
        "id": "quiet-light",
        "name": "Quiet Light",
        "preview": "Warm off-white, soft and easy on the eyes",
        "type": "light",
    },
    {
        "id": "night-owl",
        "name": "Night Owl",
        "preview": "Deep blue-based, Sarah Drasner's beloved dark theme",
        "type": "dark",
    },
    {
        "id": "light-owl",
        "name": "Light Owl",
        "preview": "Warm light, Sarah Drasner's cheerful light theme",
        "type": "light",
    },
    {
        "id": "terminal",
        "name": "Terminal",
        "preview": "Green-on-black, classic terminal aesthetic",
        "type": "dark",
    },
]


def is_light_theme(theme_name: str) -> bool:
    """Return True if the theme should use light/light colour scheme."""
    for t in THEMES:
        if t["id"] == theme_name:
            return t["type"] == "light"
    # fallback: guess from the name
    return "light" in theme_name


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

    def get_syntax_colors(self, theme_name: str) -> dict[str, str]:
        """Parse syntax highlighting colors from the theme CSS file."""
        theme_path = self.app.base_dir / "themes" / f"{theme_name}.css"
        if not theme_path.exists():
            return {}
        css = theme_path.read_text(encoding="utf-8")
        return {m.group(1): m.group(2) for m in _SYNTAX_RE.finditer(css)}

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
        if is_light_theme(theme_name):
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
