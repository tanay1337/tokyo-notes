"""Settings view for configuring application preferences."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

_THEMES: list[dict[str, str]] = [
    {"id": "tokyo-light",    "name": "Tokyo Light",    "preview": "Clean and bright, inspired by Tokyo Day",   "type": "light"},
    {"id": "tokyo-night",    "name": "Tokyo Night",    "preview": "Deep blues and vibrant accents",             "type": "dark"},
    {"id": "cyberpunk-2077", "name": "Cyberpunk 2077", "preview": "Night City vibes: Yellow, Cyan, and Black", "type": "dark"},
    {"id": "nord",           "name": "Nord",           "preview": "Arctic blue, clean and elegant",             "type": "dark"},
    {"id": "gruvbox",        "name": "Gruvbox",        "preview": "Retro warm tones, easy on the eyes",         "type": "dark"},
    {"id": "dracula",        "name": "Dracula",        "preview": "High contrast, vibrant purple tones",        "type": "dark"},
]


class SettingsView(Gtk.Box):
    """Preferences panel: folder, toggles, MCP port, and theme selection."""

    def __init__(
        self,
        on_theme_selected: Callable[[str], Any],
        on_config_changed: Callable[[str, Any], Any],
        on_select_folder_callback: Callable[[Gtk.Button], Any],
        initial_values: dict[str, Any],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("dashboard-view")

        self.on_theme_selected = on_theme_selected
        self.on_config_changed = on_config_changed
        self.on_select_folder_callback = on_select_folder_callback
        self._initial_values = initial_values

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(850)
        clamp.set_tightening_threshold(600)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(30)
        content.set_margin_bottom(30)
        content.set_margin_start(20)
        content.set_margin_end(20)

        # ---- General ----
        general_group = Adw.PreferencesGroup(title="General")
        content.append(general_group)

        self.folder_row = Adw.ActionRow(title="Notes Folder")
        self.path_label = Gtk.Label(label=initial_values.get("notes_folder", ""))
        self.path_label.add_css_class("dim-label")
        self.path_label.set_valign(Gtk.Align.CENTER)
        self.folder_row.add_suffix(self.path_label)

        folder_btn = Gtk.Button(label="Select")
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.on_select_folder_clicked)
        self.folder_row.add_suffix(folder_btn)
        general_group.add(self.folder_row)

        general_group.add(self._make_switch_row(
            "Sakura Celebration",
            "Show cherry blossoms when completing tasks",
            initial_values.get("sakura_effect", True),
            "sakura_effect",
        ))

        # ---- Toolbars ----
        toolbar_group = Adw.PreferencesGroup(title="Editor")
        content.append(toolbar_group)

        toolbar_group.add(self._make_switch_row(
            "Formatting Bar",
            "Show markdown formatting tools above the editor",
            initial_values.get("show_toolbar", True),
            "show_toolbar",
        ))
        toolbar_group.add(self._make_switch_row(
            "Status Bar",
            "Show word count and reading time at the bottom",
            initial_values.get("show_stats", False),
            "show_stats",
        ))

        # ---- AI ----
        ai_group = Adw.PreferencesGroup(title="AI")
        content.append(ai_group)

        ai_group.add(self._make_switch_row(
            "AI Bridge (MCP)",
            "Allow AI agents to read and search your notes",
            initial_values.get("mcp_server_enabled", False),
            "mcp_server_enabled",
        ))

        self.port_row = Adw.ActionRow(
            title="Bridge Port",
            subtitle="Port for the AI connection (default 8999)",
        )
        self.port_entry = Gtk.Entry()
        self.port_entry.set_text(str(initial_values.get("mcp_server_port", 8999)))
        self.port_entry.set_valign(Gtk.Align.CENTER)
        self.port_entry.set_width_chars(6)
        self.port_entry.connect("changed", self.on_port_changed)
        self.port_row.add_suffix(self.port_entry)
        ai_group.add(self.port_row)

        # ---- Themes ----
        theme_group = Adw.PreferencesGroup(title="Themes")
        content.append(theme_group)

        theme_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        theme_stack = Gtk.Stack()
        theme_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(theme_stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        theme_box.append(stack_switcher)

        self.light_theme_list = Gtk.ListBox()
        self.light_theme_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.light_theme_list.add_css_class("settings-list")

        self.dark_theme_list = Gtk.ListBox()
        self.dark_theme_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.dark_theme_list.add_css_class("settings-list")

        theme_stack.add_titled(self.dark_theme_list,  "dark",  "Dark Mode")
        theme_stack.add_titled(self.light_theme_list, "light", "Light Mode")

        self.theme_rows: dict[str, Gtk.ListBoxRow] = {}
        current_theme = initial_values.get("theme", "tokyo-night")

        for theme in _THEMES:
            row = self._make_theme_row(theme, theme["id"] == current_theme)
            target_list = self.light_theme_list if theme["type"] == "light" else self.dark_theme_list
            target_list.append(row)
            self.theme_rows[theme["id"]] = row

        theme_stack.set_visible_child_name("light" if "light" in current_theme else "dark")

        theme_box.append(theme_stack)
        theme_group.add(theme_box)

        # ---- Danger zone ----
        danger_group = Adw.PreferencesGroup(title="Reset")
        content.append(danger_group)

        reset_row = Adw.ActionRow(
            title="Reset to Defaults",
            subtitle="Restore all settings to their original values. Notes are not affected.",
        )
        reset_btn = Gtk.Button(label="Reset")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("destructive-action")
        reset_btn.connect("clicked", self.on_reset_clicked)
        reset_row.add_suffix(reset_btn)
        danger_group.add(reset_row)

        clamp.set_child(content)
        scrolled.set_child(clamp)
        self.append(scrolled)

    # ------------------------------------------------------------------ #
    # Widget factories
    # ------------------------------------------------------------------ #

    def _make_switch_row(
        self, title: str, subtitle: str, active: bool, config_key: str
    ) -> Adw.SwitchRow:
        """Create a labelled toggle row wired to *config_key*."""
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(active)
        row.connect(
            "notify::active",
            lambda r, _pspec, key=config_key: self.on_config_changed(key, r.get_active()),
        )
        return row

    def _make_theme_row(self, theme: dict[str, str], is_active: bool) -> Gtk.ListBoxRow:
        """Create a theme selection card row."""
        row = Gtk.ListBoxRow()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("theme-card")
        if is_active:
            card.add_css_class("active")

        name_label = Gtk.Label(label=theme["name"], xalign=0)
        name_label.add_css_class("theme-name")
        card.append(name_label)

        preview_label = Gtk.Label(label=theme["preview"], xalign=0)
        preview_label.add_css_class("theme-preview")
        card.append(preview_label)

        row.set_child(card)

        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", lambda *_a, tid=theme["id"]: self.select_theme(tid))
        row.add_controller(gesture)

        return row

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def on_port_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text()
        if text.isdigit():
            self.on_config_changed("mcp_server_port", int(text))

    def on_select_folder_clicked(self, button: Gtk.Button) -> None:
        self.on_select_folder_callback(button)

    def select_theme(self, theme_id: str) -> None:
        for tid, row in self.theme_rows.items():
            card = row.get_child()
            if tid == theme_id:
                card.add_css_class("active")
            else:
                card.remove_css_class("active")
        self.on_theme_selected(theme_id)

    def on_reset_clicked(self, button: Gtk.Button) -> None:
        """Reset all settings to their default values and confirm visually."""
        defaults = {
            "show_toolbar":       True,
            "show_stats":         False,
            "sakura_effect":      True,
            "mcp_server_enabled": False,
            "mcp_server_port":    8999,
            "theme":              "tokyo-night",
        }
        for key, value in defaults.items():
            self.on_config_changed(key, value)
        self.on_theme_selected("tokyo-night")

        # Briefly change the button label to confirm the reset happened.
        button.set_label("Reset ✓")
        button.set_sensitive(False)
        GLib.timeout_add(
            1500,
            lambda: (button.set_label("Reset"), button.set_sensitive(True), False)[2],
        )

    def update_folder_path(self, new_path: str) -> None:
        self.path_label.set_label(new_path)
