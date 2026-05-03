"""Settings view for configuring application preferences."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

if TYPE_CHECKING:
    from pathlib import Path

_THEMES: list[dict[str, str]] = [
    {"id": "tokyo-light",    "name": "Tokyo Light",    "preview": "Clean and bright, inspired by Tokyo Day", "type": "light"},
    {"id": "tokyo-night",    "name": "Tokyo Night",    "preview": "Deep blues and vibrant accents", "type": "dark"},
    {"id": "cyberpunk-2077", "name": "Cyberpunk 2077", "preview": "Night City vibes: Yellow, Cyan, and Black", "type": "dark"},
    {"id": "nord",           "name": "Nord",           "preview": "Arctic blue, clean and elegant", "type": "dark"},
    {"id": "gruvbox",        "name": "Gruvbox",        "preview": "Retro warm tones, easy on the eyes", "type": "dark"},
    {"id": "dracula",        "name": "Dracula",        "preview": "High contrast, vibrant purple tones", "type": "dark"},
]

class SettingsView(Gtk.Box):
    def __init__(
        self, 
        on_theme_selected: Callable[[str], Any], 
        on_config_changed: Callable[[str, Any], Any], 
        on_select_folder_callback: Callable[[Gtk.Button], Any], 
        initial_values: dict[str, Any]
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("dashboard-view")

        self.on_theme_selected = on_theme_selected
        self.on_config_changed = on_config_changed
        self.on_select_folder_callback = on_select_folder_callback
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        
        # Use Adw.Clamp to control the width of the content area
        clamp = Adw.Clamp()
        clamp.set_maximum_size(850)
        clamp.set_tightening_threshold(600)
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(30)
        content.set_margin_bottom(30)
        content.set_margin_start(20)
        content.set_margin_end(20)

        # General Section
        general_group = Adw.PreferencesGroup(title="General")
        content.append(general_group)

        # Folder Selection Row
        self.folder_row = Adw.ActionRow(title="Notes Folder")
        self.path_label = Gtk.Label(label=initial_values.get('notes_folder', ''))
        self.path_label.add_css_class("dim-label")
        self.path_label.set_valign(Gtk.Align.CENTER)
        self.folder_row.add_suffix(self.path_label)

        folder_btn = Gtk.Button(label="Select")
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.on_select_folder_clicked)
        self.folder_row.add_suffix(folder_btn)
        general_group.add(self.folder_row)

        # Sakura Effect Toggle
        sakura_row = Adw.SwitchRow(
            title="Sakura Celebration",
            subtitle="Show cherry blossoms when completing tasks"
        )
        sakura_row.set_active(initial_values.get('sakura_effect', True))
        sakura_row.connect("notify::active", lambda row, pspec: self.on_toggle_changed(row.get_active(), 'sakura_effect'))
        general_group.add(sakura_row)

        # Toolbars Section
        toolbar_group = Adw.PreferencesGroup(title="Toolbars")
        content.append(toolbar_group)

        # Formatting Bar Toggle
        formatting_row = Adw.SwitchRow(
            title="Formatting Bar",
            subtitle="Show markdown formatting tools above the editor"
        )
        formatting_row.set_active(initial_values.get('show_toolbar', True))
        formatting_row.connect("notify::active", lambda row, pspec: self.on_toggle_changed(row.get_active(), 'show_toolbar'))
        toolbar_group.add(formatting_row)

        # Status Bar Toggle
        status_row = Adw.SwitchRow(
            title="Status Bar",
            subtitle="Show word count and reading time at the bottom"
        )
        status_row.set_active(initial_values.get('show_stats', False))
        status_row.connect("notify::active", lambda row, pspec: self.on_toggle_changed(row.get_active(), 'show_stats'))
        toolbar_group.add(status_row)

        # AI Section
        ai_group = Adw.PreferencesGroup(title="AI")
        content.append(ai_group)

        # AI Bridge Toggle
        ai_bridge_row = Adw.SwitchRow(
            title="AI Bridge (MCP)",
            subtitle="Allow AI agents to read and search your notes"
        )
        ai_bridge_row.set_active(initial_values.get('mcp_server_enabled', False))
        ai_bridge_row.connect("notify::active", lambda row, pspec: self.on_toggle_changed(row.get_active(), 'mcp_server_enabled'))
        ai_group.add(ai_bridge_row)

        # Port Selection Row
        self.port_row = Adw.ActionRow(
            title="Bridge Port",
            subtitle="Port for the AI connection (default 8999)"
        )
        self.port_entry = Gtk.Entry()
        self.port_entry.set_text(str(initial_values.get('mcp_server_port', 8999)))
        self.port_entry.set_valign(Gtk.Align.CENTER)
        self.port_entry.set_width_chars(6)
        self.port_entry.connect("changed", self.on_port_changed)
        self.port_row.add_suffix(self.port_entry)
        ai_group.add(self.port_row)

        # Theme Section
        theme_group = Adw.PreferencesGroup(title="Themes")
        content.append(theme_group)

        theme_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        
        # Tabs for Light/Dark
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
        
        theme_stack.add_titled(self.dark_theme_list, "dark", "Dark Mode")
        theme_stack.add_titled(self.light_theme_list, "light", "Light Mode")

        self.theme_rows: dict[str, Gtk.ListBoxRow] = {}
        current_theme = initial_values.get('theme', 'tokyo-night')
        
        for theme in _THEMES:
            row = self.create_theme_row(theme, theme["id"] == current_theme)
            if theme["type"] == "light":
                self.light_theme_list.append(row)
            else:
                self.dark_theme_list.append(row)
            self.theme_rows[theme["id"]] = row
        
        if "light" in current_theme:
            theme_stack.set_visible_child_name("light")
        else:
            theme_stack.set_visible_child_name("dark")

        theme_box.append(theme_stack)
        theme_group.add(theme_box)

        clamp.set_child(content)
        scrolled.set_child(clamp)
        self.append(scrolled)

    def create_theme_row(self, theme: dict[str, str], is_active: bool) -> Gtk.ListBoxRow:
        """Creates a theme selection row."""
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
        gesture.connect("pressed", lambda g, n, x, y: self.select_theme(theme["id"]))
        row.add_controller(gesture)
        
        return row

    def on_port_changed(self, entry: Gtk.Entry) -> None:
        """Handles MCP port change."""
        text = entry.get_text()
        if text.isdigit():
            self.on_config_changed('mcp_server_port', int(text))

    def on_toggle_changed(self, state: bool, config_key: str) -> None:
        """Handles UI toggles."""
        self.on_config_changed(config_key, state)

    def update_folder_path(self, new_path: str) -> None:
        """Updates the displayed folder path."""
        self.path_label.set_label(new_path)

    def on_select_folder_clicked(self, button: Gtk.Button) -> None:
        """Triggers folder select dialog."""
        self.on_select_folder_callback(button)

    def select_theme(self, theme_id: str) -> None:
        """Selects a theme."""
        for tid, row in self.theme_rows.items():
            card = row.get_child()
            if tid == theme_id:
                card.add_css_class("active")
            else:
                card.remove_css_class("active")
        
        self.on_theme_selected(theme_id)
