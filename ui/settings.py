"""Settings view for configuring application preferences."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from core.utils import clear_listbox, confirm_destructive_dialog

logger = logging.getLogger(__name__)

_THEMES: list[dict[str, str]] = [
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
]


class SettingsView(Gtk.Box):
    """Preferences panel: folder, toggles, and theme selection."""

    def __init__(
        self,
        on_theme_selected: Callable[[str], Any],
        on_config_changed: Callable[[str, Any], Any],
        on_select_folder_callback: Callable[[Gtk.Button], Any],
        initial_values: dict[str, Any],
        on_change_password: Callable[[], Any] | None = None,
        on_set_password: Callable[[str], Any] | None = None,
        on_new_template: Callable[[], Any] | None = None,
        on_edit_template: Callable[[str], Any] | None = None,
        on_delete_template: Callable[[str], Any] | None = None,
        on_open_templates_folder: Callable[[], Any] | None = None,
        templates: list[dict[str, str]] | None = None,
        assets_dir: Path | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.on_theme_selected = on_theme_selected
        self.on_config_changed = on_config_changed
        self.on_select_folder_callback = on_select_folder_callback
        self._initial_values = initial_values
        self._assets_dir = assets_dir
        self._on_change_password = on_change_password
        self._on_set_password = on_set_password
        self._has_encrypted_notes = initial_values.get("has_encrypted_notes", False)
        self._on_new_template = on_new_template
        self._on_edit_template = on_edit_template
        self._on_delete_template = on_delete_template
        self._on_open_templates_folder = on_open_templates_folder
        self._templates = templates or []

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

        content.append(self._build_general_group())
        content.append(self._build_editor_group())
        content.append(self._build_dashboard_group())
        content.append(self._build_private_group())
        content.append(self._build_templates_group())
        content.append(self._build_theme_group())
        content.append(self._build_danger_group())

        clamp.set_child(content)
        scrolled.set_child(clamp)
        self.append(scrolled)

    # Group builders

    def _build_general_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="General")

        self.folder_row = Adw.ActionRow(title="Notes Folder")
        self.path_label = Gtk.Label(label=self._initial_values.get("notes_folder", ""))
        self.path_label.add_css_class("dim-label")
        self.path_label.set_valign(Gtk.Align.CENTER)
        self.folder_row.add_suffix(self.path_label)

        folder_btn = Gtk.Button(label="Select")
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.on_select_folder_clicked)
        self.folder_row.add_suffix(folder_btn)
        group.add(self.folder_row)

        group.add(
            self._make_switch_row(
                "Sakura Celebration",
                "Show cherry blossoms when completing tasks",
                self._initial_values.get("sakura_effect", True),
                "sakura_effect",
            )
        )
        return group

    def _build_editor_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Editor")
        group.add(
            self._make_switch_row(
                "Formatting Bar",
                "Show markdown formatting tools above the editor",
                self._initial_values.get("show_toolbar", True),
                "show_toolbar",
            )
        )
        group.add(
            self._make_switch_row(
                "Status Bar",
                "Show word count and reading time at the bottom",
                self._initial_values.get("show_stats", False),
                "show_stats",
            )
        )
        group.add(
            self._make_switch_row(
                "Backlinks Button",
                "Show floating backlinks button in the editor",
                self._initial_values.get("show_backlinks", True),
                "show_backlinks",
            )
        )
        return group

    def _build_dashboard_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Dashboard")
        group.add(
            self._make_switch_row(
                "Show Completed Tasks",
                "Include completed tasks in the dashboard",
                self._initial_values.get("show_completed", True),
                "show_completed",
            )
        )
        group.add(
            self._make_switch_row(
                "Progress Indicators",
                "Show completion rings on date headers",
                self._initial_values.get("show_progress_rings", True),
                "show_progress_rings",
            )
        )
        group.add(
            self._make_switch_row(
                "Start Week on Sunday",
                "Show Sun - Sat instead of Mon - Sun in the Week filter",
                self._initial_values.get("start_week_on_sunday", True),
                "start_week_on_sunday",
            )
        )
        return group

    def _build_private_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Private Notes")

        self._change_password_row = Adw.ActionRow(
            title="Master password",
            subtitle="Make a note private first to enable private notes."
            if not self._has_encrypted_notes
            else "",
        )
        self._change_password_btn = Gtk.Button(
            label="Set password" if not self._has_encrypted_notes else "Change password"
        )
        self._change_password_btn.set_valign(Gtk.Align.CENTER)
        self._change_password_btn.set_sensitive(self._has_encrypted_notes)
        self._change_password_btn.connect("clicked", self._on_change_password_clicked)
        self._change_password_row.add_suffix(self._change_password_btn)
        group.add(self._change_password_row)

        self._lock_timeout_row = self._make_lock_timeout_row(
            self._initial_values.get("lock_timeout_minutes", 5),
            self.on_config_changed,
        )
        group.add(self._lock_timeout_row)
        return group

    def _build_templates_group(self) -> Adw.PreferencesGroup:
        self._templates_group = Adw.PreferencesGroup(title="Templates")

        if self._on_new_template:
            sub_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            sub_header_box.set_margin_start(12)
            sub_header_box.set_margin_end(12)
            sub_header_box.set_margin_top(16)
            sub_header_box.set_margin_bottom(6)

            sub_label = Gtk.Label(label="Current Templates", xalign=0)
            sub_label.add_css_class("template-subheading")
            sub_label.set_hexpand(True)
            sub_header_box.append(sub_label)

            new_btn = Gtk.Button()
            new_btn.set_valign(Gtk.Align.CENTER)
            new_btn.set_tooltip_text("New Template")
            new_btn.add_css_class("settings-icon-btn")
            new_img = Gtk.Image.new_from_file(
                str(self._assets_dir / "new-template.svg")
            )
            new_img.set_pixel_size(16)
            new_btn.set_child(new_img)
            new_btn.connect("clicked", lambda _: self._on_new_template())
            sub_header_box.append(new_btn)

            self._templates_group.add(sub_header_box)

        self._templates_list = Gtk.ListBox()
        self._templates_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._templates_group.add(self._templates_list)
        self._template_rows: list[Gtk.ListBoxRow] = []
        self._populate_templates()

        if self._on_open_templates_folder:
            folder_row = Adw.ActionRow(
                title="Templates Folder",
                subtitle="Open templates directory in file manager",
            )
            folder_btn = Gtk.Button()
            folder_btn.set_valign(Gtk.Align.CENTER)
            folder_btn.add_css_class("settings-icon-btn")
            folder_img = Gtk.Image.new_from_file(
                str(self._assets_dir / "open-folder.svg")
            )
            folder_img.set_pixel_size(16)
            folder_btn.set_child(folder_img)
            folder_btn.connect("clicked", lambda _: self._on_open_templates_folder())
            folder_row.add_suffix(folder_btn)
            self._templates_group.add(folder_row)

        return self._templates_group

    def _build_theme_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Themes")

        theme_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        theme_stack = Gtk.Stack()
        theme_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(theme_stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        theme_box.append(stack_switcher)

        self.light_theme_list = Gtk.ListBox()
        self.light_theme_list.set_selection_mode(Gtk.SelectionMode.NONE)

        self.dark_theme_list = Gtk.ListBox()
        self.dark_theme_list.set_selection_mode(Gtk.SelectionMode.NONE)

        theme_stack.add_titled(self.dark_theme_list, "dark", "Dark Mode")
        theme_stack.add_titled(self.light_theme_list, "light", "Light Mode")

        self.theme_rows: dict[str, Gtk.ListBoxRow] = {}
        current_theme = self._initial_values.get("theme", "tokyo-night")

        for theme in _THEMES:
            row = self._make_theme_row(theme, theme["id"] == current_theme)
            target_list = (
                self.light_theme_list
                if theme["type"] == "light"
                else self.dark_theme_list
            )
            target_list.append(row)
            self.theme_rows[theme["id"]] = row

        theme_stack.set_visible_child_name(
            "light" if "light" in current_theme else "dark"
        )
        theme_box.append(theme_stack)
        group.add(theme_box)
        return group

    def _build_danger_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Reset")

        reset_row = Adw.ActionRow(
            title="Reset to Defaults",
            subtitle="Restore all settings to their original values."
            " Notes are not affected.",
        )
        reset_btn = Gtk.Button(label="Reset")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("destructive-action")
        reset_btn.connect("clicked", self.on_reset_clicked)
        reset_row.add_suffix(reset_btn)
        group.add(reset_row)
        return group

    # Widget factories

    def _make_switch_row(
        self, title: str, subtitle: str, active: bool, config_key: str
    ) -> Adw.SwitchRow:
        """Create a labelled toggle row wired to *config_key*."""
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(active)
        row.connect(
            "notify::active",
            lambda r, _pspec, key=config_key: self.on_config_changed(
                key, r.get_active()
            ),
        )
        return row

    def _make_lock_timeout_row(
        self, current_minutes: int, on_config_changed: Callable[[str, Any], Any]
    ) -> Adw.ComboRow:
        """Create a dropdown for lock timeout selection."""
        options = {
            0: "Never",
            5: "5 min",
            15: "15 min",
            30: "30 min",
            60: "1 hour",
        }
        model = Gtk.StringList()
        for minutes in (0, 5, 15, 30, 60):
            model.append(options[minutes])

        row = Adw.ComboRow(title="Lock after inactivity", model=model)
        current_idx = (
            (0, 5, 15, 30, 60).index(current_minutes)
            if current_minutes in options
            else 1
        )
        row.set_selected(current_idx)
        row.connect(
            "notify::selected",
            lambda r, _pspec: on_config_changed(
                "lock_timeout_minutes",
                (0, 5, 15, 30, 60)[r.get_selected()],
            ),
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

    # Event handlers

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
            "show_toolbar": True,
            "show_stats": False,
            "sakura_effect": True,
            "show_completed": True,
            "show_progress_rings": True,
            "show_backlinks": True,
            "start_week_on_sunday": True,
            "theme": "tokyo-night",
        }
        for key, value in defaults.items():
            self.on_config_changed(key, value)
        self.on_theme_selected("tokyo-night")

        button.set_label("Reset ✓")
        button.set_sensitive(False)

        def _reset_btn() -> bool:
            button.set_label("Reset")
            button.set_sensitive(True)
            return False

        GLib.timeout_add(1500, _reset_btn)

    def update_folder_path(self, new_path: str) -> None:
        self.path_label.set_label(new_path)

    def refresh_privacy_state(self, has_encrypted: bool) -> None:
        self._has_encrypted_notes = has_encrypted
        self._change_password_btn.set_label(
            "Set password" if not has_encrypted else "Change password"
        )
        self._change_password_btn.set_sensitive(has_encrypted)
        self._change_password_row.set_subtitle(
            "Make a note private first to enable private notes."
            if not has_encrypted
            else ""
        )

    def _on_change_password_clicked(self, *_args) -> None:
        if self._has_encrypted_notes:
            if self._on_change_password:
                self._on_change_password()

    def _populate_templates(self) -> None:
        """Populate the templates list box with action rows."""
        clear_listbox(self._templates_list)
        self._template_rows = []

        if not self._templates:
            empty = Gtk.ListBoxRow()
            empty.set_sensitive(False)
            label = Gtk.Label(
                label="No templates yet. Click the + button to create one.", xalign=0.5
            )
            label.add_css_class("dim-label")
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            empty.set_child(label)
            self._templates_list.append(empty)
            return

        for tmpl in self._templates:
            row = self._make_template_row(tmpl)
            self._templates_list.append(row)
            self._template_rows.append(row)

    def _make_template_row(self, tmpl: dict[str, str]) -> Gtk.ListBoxRow:
        """Create a template list row with name, edit button, and delete button."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        label = Gtk.Label(label=tmpl["name"], xalign=0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(label)

        if tmpl.get("is_builtin"):
            badge = Gtk.Label(label="Built-in")
            badge.add_css_class("template-badge")
            box.append(badge)

        edit_btn = Gtk.Button(label="Edit")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.add_css_class("template-action-btn")
        edit_btn.connect("clicked", lambda _: self._on_edit_template(tmpl["slug"]))
        box.append(edit_btn)

        delete_btn = Gtk.Button()
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.add_css_class("template-action-btn")
        delete_btn.add_css_class("settings-icon-btn")
        delete_img = Gtk.Image.new_from_file(str(self._assets_dir / "delete.svg"))
        delete_img.set_pixel_size(16)
        delete_btn.set_child(delete_img)
        delete_btn.connect(
            "clicked",
            lambda _: self._on_delete_template_confirm(tmpl["slug"], tmpl["name"]),
        )
        box.append(delete_btn)

        row.set_child(box)
        return row

    def _on_delete_template_confirm(self, slug: str, name: str) -> None:
        """Show confirmation before deleting a template."""
        dialog = confirm_destructive_dialog(
            transient_for=self.get_root(),
            heading="Delete Template?",
            body=f"Are you sure you want to delete '{name}'?"
            " This action cannot be undone.",
        )
        dialog.connect("response", self._on_delete_template_response, slug)
        dialog.present()

    def _on_delete_template_response(
        self, dialog: Adw.MessageDialog, response: str, slug: str
    ) -> None:
        if response == "delete" and self._on_delete_template:
            if self._on_delete_template(slug):
                self._templates = [t for t in self._templates if t["slug"] != slug]
                self._populate_templates()

    def refresh_templates(self, templates: list[dict[str, str]]) -> None:
        """Refresh the templates list with new data."""
        self._templates = templates
        self._populate_templates()
