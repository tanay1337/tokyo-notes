"""Settings view for configuring application preferences."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango
from spellchecker import SpellChecker as PySpellChecker

from core.theme_manager import THEMES
from core.translations import list_languages, tr
from core.utils import clear_listbox, confirm_destructive_dialog

logger = logging.getLogger(__name__)


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
        on_restore_builtins: Callable[[], Any] | None = None,
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
        self._on_restore_builtins = on_restore_builtins
        self._templates = templates or []
        self._git_available = initial_values.get("git_available", False)
        self._switch_rows: dict[str, Adw.SwitchRow] = {}

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(850)
        clamp.set_tightening_threshold(600)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self._content.set_margin_top(30)
        self._content.set_margin_bottom(30)
        self._content.set_margin_start(20)
        self._content.set_margin_end(20)

        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text(tr("Search settings"))
        search_entry.set_margin_top(5)
        search_entry.set_margin_bottom(5)
        search_entry.set_margin_start(5)
        search_entry.set_margin_end(5)
        search_entry.connect("search-changed", self._on_settings_search)
        self._content.append(search_entry)

        self._settings_groups: list[Adw.PreferencesGroup] = []

        self._general_group = self._build_general_group()
        self._content.append(self._general_group)
        self._settings_groups.append(self._general_group)

        self._editor_group = self._build_editor_group()
        self._content.append(self._editor_group)
        self._settings_groups.append(self._editor_group)

        self._dashboard_group = self._build_dashboard_group()
        self._content.append(self._dashboard_group)
        self._settings_groups.append(self._dashboard_group)

        self._versioning_group = self._build_versioning_group()
        self._content.append(self._versioning_group)
        self._settings_groups.append(self._versioning_group)

        self._private_group = self._build_private_group()
        self._content.append(self._private_group)
        self._settings_groups.append(self._private_group)

        self._templates_group_container = self._build_templates_group()
        self._content.append(self._templates_group_container)
        self._settings_groups.append(self._templates_group_container)

        self._theme_group = self._build_theme_group()
        self._content.append(self._theme_group)
        self._settings_groups.append(self._theme_group)

        self._danger_group = self._build_danger_group()
        self._content.append(self._danger_group)
        self._settings_groups.append(self._danger_group)

        clamp.set_child(self._content)
        scrolled.set_child(clamp)
        self.append(scrolled)

    # Group builders

    def _build_general_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("General"))

        self.folder_row = Adw.ActionRow(title=tr("Notes Folder"))
        self.path_label = Gtk.Label(label=self._initial_values.get("notes_folder", ""))
        self.path_label.add_css_class("dim-label")
        self.path_label.set_valign(Gtk.Align.CENTER)
        self.folder_row.add_suffix(self.path_label)

        folder_btn = Gtk.Button(label=tr("Select"))
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.on_select_folder_clicked)
        self.folder_row.add_suffix(folder_btn)
        group.add(self.folder_row)

        group.add(
            self._make_switch_row(
                tr("Sakura Celebration"),
                tr("Show cherry blossoms when completing tasks"),
                self._initial_values.get("sakura_effect", True),
                "sakura_effect",
            )
        )

        group.add(self._make_font_row())
        group.add(self._make_font_size_row())
        group.add(self._make_language_row())
        return group

    def _build_editor_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("Editor"))
        group.add(
            self._make_switch_row(
                tr("Always Show Markdown"),
                tr("Makes markdown formatting characters always visible on all lines"),
                self._initial_values.get("always_show_markdown", False),
                "always_show_markdown",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Formatting Bar"),
                tr("Show markdown formatting tools above the editor"),
                self._initial_values.get("show_toolbar", True),
                "show_toolbar",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Status Bar"),
                tr("Show word count and reading time at the bottom"),
                self._initial_values.get("show_stats", False),
                "show_stats",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Backlinks Button"),
                tr("Show floating backlinks button in the editor"),
                self._initial_values.get("show_backlinks", True),
                "show_backlinks",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Create Notes from Links"),
                tr("Clicking a link to a non-existent note creates it automatically"),
                self._initial_values.get("create_on_link_click", True),
                "create_on_link_click",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Spell Check"),
                tr("Highlight misspelled words with a red squiggly underline"),
                self._initial_values.get("spell_check_enabled", True),
                "spell_check_enabled",
            )
        )
        group.add(self._make_spell_language_row())
        return group

    def _build_dashboard_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("Dashboard"))
        group.add(
            self._make_switch_row(
                tr("Show Completed Tasks"),
                tr("Include completed tasks in the dashboard"),
                self._initial_values.get("show_completed", True),
                "show_completed",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Progress Indicators"),
                tr("Show completion rings on date headers"),
                self._initial_values.get("show_progress_rings", True),
                "show_progress_rings",
            )
        )
        group.add(
            self._make_switch_row(
                tr("Start Week on Sunday"),
                tr("Show Sun - Sat instead of Mon - Sun in the Week filter"),
                self._initial_values.get("start_week_on_sunday", True),
                "start_week_on_sunday",
            )
        )
        return group

    def _build_versioning_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("Versioning"))

        if not self._git_available:
            status_row = Adw.ActionRow(
                title=tr("Git Versioning"),
                subtitle=tr("git not found on this system — install git to enable"),
            )
            status_row.set_sensitive(False)
            group.add(status_row)
            return group

        self._git_enabled_row = self._make_switch_row(
            tr("Git Versioning"),
            tr("Track changes with git in your notes folder"),
            self._initial_values.get("git_enabled", False),
            "git_enabled",
        )
        group.add(self._git_enabled_row)

        self._git_auto_commit_row = self._make_switch_row(
            tr("Auto-commit on save"),
            tr("Create a git commit every time a note is saved"),
            self._initial_values.get("git_auto_commit", True),
            "git_auto_commit",
        )
        group.add(self._git_auto_commit_row)

        return group

    def _build_private_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("Private Notes"))

        self._change_password_row = Adw.ActionRow(
            title=tr("Master password"),
            subtitle=tr("Make a note private first to enable private notes.")
            if not self._has_encrypted_notes
            else "",
        )
        label = (
            tr("Set password")
            if not self._has_encrypted_notes
            else tr("Change password")
        )
        self._change_password_btn = Gtk.Button(label=label)
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
        self._templates_group = Adw.PreferencesGroup(title=tr("Templates"))

        if self._on_new_template:
            sub_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            sub_header_box.set_margin_start(12)
            sub_header_box.set_margin_end(12)
            sub_header_box.set_margin_top(16)
            sub_header_box.set_margin_bottom(6)

            sub_label = Gtk.Label(label=tr("Current Templates"), xalign=0)
            sub_label.add_css_class("template-subheading")
            sub_label.set_hexpand(True)
            sub_header_box.append(sub_label)

            new_btn = Gtk.Button()
            new_btn.set_valign(Gtk.Align.CENTER)
            new_btn.set_tooltip_text(tr("New Template"))
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
                title=tr("Templates Folder"),
                subtitle=tr("Open templates directory in file manager"),
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

        if self._on_restore_builtins:
            restore_row = Adw.ActionRow(
                title=tr("Restore Built-in Templates"),
                subtitle=tr(
                    "Reset all built-in templates to their original content."
                    " Custom templates will not be affected."
                ),
            )
            restore_btn = Gtk.Button(label=tr("Restore"))
            restore_btn.set_valign(Gtk.Align.CENTER)
            restore_btn.add_css_class("template-action-btn")
            restore_btn.connect("clicked", self._on_restore_builtins_clicked)
            restore_row.add_suffix(restore_btn)
            self._templates_group.add(restore_row)

        return self._templates_group

    def _on_restore_builtins_clicked(self, _btn: Gtk.Button) -> None:
        """Show confirmation before restoring built-in templates."""
        dialog = confirm_destructive_dialog(
            transient_for=self.get_root(),
            heading=tr("Restore Built-in Templates?"),
            body=tr(
                "This will reset all built-in templates to their"
                " original content. Custom templates are not affected."
            ),
            confirm_label=tr("Restore"),
        )
        dialog.connect("response", self._on_restore_builtins_response)
        dialog.present()

    def _on_restore_builtins_response(
        self, dialog: Adw.MessageDialog, response: str
    ) -> None:
        if response == "delete" and self._on_restore_builtins:
            self._on_restore_builtins()

    def _build_theme_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("Themes"))

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

        theme_stack.add_titled(self.dark_theme_list, "dark", tr("Dark Mode"))
        theme_stack.add_titled(self.light_theme_list, "light", tr("Light Mode"))

        self.theme_rows: dict[str, Gtk.ListBoxRow] = {}
        self._theme_expanded = {"light": False, "dark": False}
        current_theme = self._initial_values.get("theme", "tokyo-night")

        light_themes = [t for t in THEMES if t["type"] == "light"]
        dark_themes = [t for t in THEMES if t["type"] == "dark"]

        self._populate_theme_list(
            self.light_theme_list, light_themes, current_theme, "light"
        )
        self._populate_theme_list(
            self.dark_theme_list, dark_themes, current_theme, "dark"
        )

        theme_stack.set_visible_child_name(
            "light" if "light" in current_theme else "dark"
        )
        theme_box.append(theme_stack)
        group.add(theme_box)
        return group

    def _populate_theme_list(
        self,
        list_box: Gtk.ListBox,
        themes: list[dict[str, str]],
        current_theme: str,
        theme_type: str,
    ) -> None:
        visible_count = 5
        expanded = self._theme_expanded[theme_type]

        for i, theme in enumerate(themes):
            row = self._make_theme_row(theme, theme["id"] == current_theme)
            row.set_visible(expanded or i < visible_count)
            list_box.append(row)
            self.theme_rows[theme["id"]] = row

        if len(themes) > visible_count:
            remaining = len(themes) - visible_count
            btn_label = (
                tr("Show Less") if expanded else tr("Show {n} More").format(n=remaining)
            )
            show_more_btn = Gtk.Button(label=btn_label)
            show_more_btn.add_css_class("flat")
            show_more_btn.set_halign(Gtk.Align.CENTER)
            show_more_btn.set_margin_top(4)
            show_more_btn.connect(
                "clicked",
                lambda _btn, lb=list_box, th=themes, ct=current_theme, tt=theme_type: (
                    self._toggle_theme_list(lb, th, ct, tt)
                ),
            )
            list_box.append(show_more_btn)

    def _toggle_theme_list(
        self,
        list_box: Gtk.ListBox,
        themes: list[dict[str, str]],
        current_theme: str,
        theme_type: str,
    ) -> None:
        self._theme_expanded[theme_type] = not self._theme_expanded[theme_type]
        visible_count = 5
        expanded = self._theme_expanded[theme_type]

        from core.utils import clear_listbox

        clear_listbox(list_box)

        for i, theme in enumerate(themes):
            row = self._make_theme_row(theme, theme["id"] == current_theme)
            row.set_visible(expanded or i < visible_count)
            list_box.append(row)
            self.theme_rows[theme["id"]] = row

        remaining = len(themes) - visible_count
        btn_label = (
            tr("Show Less") if expanded else tr("Show {n} More").format(n=remaining)
        )
        show_more_btn = Gtk.Button(label=btn_label)
        show_more_btn.add_css_class("flat")
        show_more_btn.set_halign(Gtk.Align.CENTER)
        show_more_btn.set_margin_top(4)
        show_more_btn.connect(
            "clicked",
            lambda _btn, lb=list_box, th=themes, ct=current_theme, tt=theme_type: (
                self._toggle_theme_list(lb, th, ct, tt)
            ),
        )
        list_box.append(show_more_btn)

    def _build_danger_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=tr("Reset"))

        reset_row = Adw.ActionRow(
            title=tr("Reset to Defaults"),
            subtitle=tr(
                "Restore all settings to their original values. Notes are not affected."
            ),
        )
        reset_btn = Gtk.Button(label=tr("Reset"))
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
        self._switch_rows[config_key] = row
        return row

    def _make_lock_timeout_row(
        self, current_minutes: int, on_config_changed: Callable[[str, Any], Any]
    ) -> Adw.ComboRow:
        """Create a dropdown for lock timeout selection."""
        options = {
            0: tr("Never"),
            5: tr("5 min"),
            15: tr("15 min"),
            30: tr("30 min"),
            60: tr("1 hour"),
        }
        model = Gtk.StringList()
        for minutes in (0, 5, 15, 30, 60):
            model.append(options[minutes])

        row = Adw.ComboRow(title=tr("Lock after inactivity"), model=model)
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

    def _make_font_row(self) -> Adw.ComboRow:
        """Create a dropdown listing all installed system fonts."""
        families: list[str] = []
        try:
            context = self.get_pango_context()
            font_map = context.get_font_map()
            if font_map is not None:
                families = sorted(f.get_name() for f in font_map.list_families())
        except Exception:
            families = []

        model = Gtk.StringList()
        model.append(tr("System Default"))
        for name in families:
            model.append(name)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_font_item_setup)
        factory.connect("bind", self._on_font_item_bind)
        factory.connect("unbind", self._on_font_item_unbind)

        self._font_row = Adw.ComboRow(
            title=tr("App Font"),
            subtitle=tr("Font used throughout the application"),
            model=model,
            factory=factory,
        )

        current = self._initial_values.get("font_family")
        if current is None:
            self._font_row.set_selected(0)
        else:
            try:
                self._font_row.set_selected(families.index(current) + 1)
            except ValueError:
                self._font_row.set_selected(0)

        self._font_row.connect(
            "notify::selected",
            lambda r, _pspec: self.on_config_changed(
                "font_family",
                None
                if r.get_selected() == 0
                else r.get_model().get_string(r.get_selected()),
            ),
        )
        return self._font_row

    @staticmethod
    def _on_font_item_setup(
        factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        label = Gtk.Label(xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        list_item.set_child(label)

    def _on_font_item_bind(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        label: Gtk.Label = list_item.get_child()
        string_obj = list_item.get_item()
        name = string_obj.get_string() if string_obj else ""
        label.set_text(name)
        if name and name != "System Default":
            desc = Pango.FontDescription.from_string(name)
            desc.set_size(11 * Pango.SCALE)
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_font_desc_new(desc))
            label.set_attributes(attrs)
        else:
            label.set_attributes(Pango.AttrList())

    @staticmethod
    def _on_font_item_unbind(
        factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        label: Gtk.Label = list_item.get_child()
        label.set_attributes(Pango.AttrList())

    def _make_font_size_row(self) -> Adw.ActionRow:
        """Create a row with a spin button and reset for the base font size."""
        subtitle = tr("Base font size for the application")
        row = Adw.ActionRow(title=tr("Font Size"), subtitle=subtitle)

        self._font_size_spin = Gtk.SpinButton.new_with_range(8, 24, 1)
        self._font_size_spin.set_valign(Gtk.Align.CENTER)
        current = self._initial_values.get("font_size")
        if current is not None:
            self._font_size_spin.set_value(float(current))
        else:
            self._font_size_spin.set_value(12)
        self._font_size_handler_id = self._font_size_spin.connect(
            "notify::value",
            lambda s, _pspec: self.on_config_changed("font_size", int(s.get_value())),
        )
        row.add_suffix(self._font_size_spin)

        reset_btn = Gtk.Button()
        reset_icon = Gtk.Image.new_from_file(str(self._assets_dir / "undo.svg"))
        reset_icon.set_pixel_size(16)
        reset_btn.set_child(reset_icon)
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        reset_btn.add_css_class("settings-icon-btn")
        reset_btn.set_tooltip_text(tr("Reset to default"))
        reset_btn.connect("clicked", lambda _: self._on_font_size_reset())
        row.add_suffix(reset_btn)

        return row

    def _on_font_size_reset(self) -> None:
        self._font_size_spin.set_value(12)
        self.on_config_changed("font_size", None)

    def _make_language_row(self) -> Adw.ComboRow:
        """Create a dropdown listing available translation languages."""
        languages = list_languages()
        model = Gtk.StringList()
        self._lang_codes: list[str] = []
        for code in sorted(languages):
            model.append(languages[code])
            self._lang_codes.append(code)

        current = self._initial_values.get("language", "en")
        try:
            idx = self._lang_codes.index(current)
        except ValueError:
            idx = self._lang_codes.index("en")

        self._language_row = Adw.ComboRow(
            title=tr("Language"),
            subtitle=tr("App language (requires restart)"),
            model=model,
        )
        self._language_row.set_selected(idx)
        self._language_row.connect(
            "notify::selected",
            lambda r, _pspec: self.on_config_changed(
                "language", self._lang_codes[r.get_selected()]
            ),
        )
        return self._language_row

    def _make_spell_language_row(self) -> Adw.ComboRow:
        """Create a dropdown for spell-check language selection."""
        sp_langs = PySpellChecker.languages()
        model = Gtk.StringList()
        for code in sp_langs:
            model.append(code)
        current = self._initial_values.get("spell_check_language", "en")
        try:
            idx = sp_langs.index(current)
        except ValueError:
            idx = sp_langs.index("en") if "en" in sp_langs else 0

        self._spell_lang_codes = sp_langs
        self._spell_language_row = Adw.ComboRow(
            title=tr("Spell Check Language"),
            subtitle=tr("Dictionary language for spell checking"),
            model=model,
        )
        self._spell_language_row.set_selected(idx)
        self._spell_language_row.connect(
            "notify::selected",
            lambda r, _pspec: self.on_config_changed(
                "spell_check_language", self._spell_lang_codes[r.get_selected()]
            ),
        )
        return self._spell_language_row

    def _make_theme_row(self, theme: dict[str, str], is_active: bool) -> Gtk.ListBoxRow:
        """Create a theme selection card row with color palette preview."""
        row = Gtk.ListBoxRow()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("theme-card")
        if is_active:
            card.add_css_class("active")

        palette = self._get_theme_palette(theme["id"])
        if palette:
            swatch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            swatch_box.set_margin_bottom(4)
            for color in palette:
                swatch = Gtk.Box()
                swatch.set_size_request(20, 12)
                swatch.set_halign(Gtk.Align.START)
                css = (
                    f"box {{ background-color: {color}; border-radius: 3px;"
                    " min-width: 20px; min-height: 12px; }"
                )
                provider = Gtk.CssProvider()
                provider.load_from_string(css)
                swatch.get_style_context().add_provider(
                    provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                swatch_box.append(swatch)
            card.append(swatch_box)

        name_label = Gtk.Label(label=tr(theme["name"]), xalign=0)
        name_label.add_css_class("theme-name")
        card.append(name_label)

        preview_label = Gtk.Label(label=tr(theme["preview"]), xalign=0)
        preview_label.add_css_class("theme-preview")
        card.append(preview_label)

        row.set_child(card)

        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", lambda *_a, tid=theme["id"]: self.select_theme(tid))
        row.add_controller(gesture)

        return row

    @staticmethod
    def _get_theme_palette(theme_id: str) -> list[str]:
        """Extract key palette colors from the theme CSS file."""
        import re
        from pathlib import Path

        css_path = Path(__file__).resolve().parent.parent / "themes" / f"{theme_id}.css"
        if not css_path.exists():
            return []
        css = css_path.read_text(encoding="utf-8")
        vars_ = dict(re.findall(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]+)\s*;", css))
        colors = []
        for key in ("bg_color", "accent_color", "fg_color", "selection_color"):
            if key in vars_:
                colors.append(vars_[key])
        return colors

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

    def _on_settings_search(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().lower()
        if not query:
            for group in self._settings_groups:
                group.set_visible(True)
            return

        for group in self._settings_groups:
            group.set_visible(self._settings_group_matches(group, query))

    def _settings_group_matches(self, group: Adw.PreferencesGroup, query: str) -> bool:
        if query in group.get_title().lower():
            return True
        child = group.get_first_child()
        while child:
            title = getattr(child, "get_title", None)
            if title:
                val = title()
                if val and query in val.lower():
                    return True
            subtitle = getattr(child, "get_subtitle", None)
            if subtitle:
                val = subtitle()
                if val and query in val.lower():
                    return True
            child = child.get_next_sibling()
        return False

    def on_reset_clicked(self, button: Gtk.Button) -> None:
        """Reset all settings to their default values and confirm visually."""
        defaults = {
            "show_toolbar": True,
            "show_stats": False,
            "sakura_effect": True,
            "show_completed": True,
            "show_progress_rings": True,
            "show_backlinks": True,
            "git_enabled": False,
            "git_auto_commit": True,
            "theme": "tokyo-night",
            "font_family": None,
            "font_size": None,
            "language": "en",
            "spell_check_enabled": True,
            "spell_check_language": "en",
        }
        for key, value in defaults.items():
            self.on_config_changed(key, value)
        self.select_theme("tokyo-night")

        for key, value in defaults.items():
            if key in self._switch_rows:
                self._switch_rows[key].set_active(value)
        self._font_row.set_selected(0)
        self._font_size_spin.handler_block(self._font_size_handler_id)
        self._font_size_spin.set_value(12)
        self._font_size_spin.handler_unblock(self._font_size_handler_id)

        try:
            lang_idx = self._lang_codes.index("en")
            self._language_row.set_selected(lang_idx)
        except ValueError:
            pass

        try:
            sp_idx = self._spell_lang_codes.index("en")
            self._spell_language_row.set_selected(sp_idx)
        except ValueError:
            pass

        button.set_label("Reset ✓")
        button.set_sensitive(False)

        def _reset_btn() -> bool:
            button.set_label(tr("Reset"))
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
                label=tr("No templates yet. Click the + button to create one."),
                xalign=0.5,
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
            badge = Gtk.Label(label=tr("Built-in"))
            badge.add_css_class("template-badge")
            box.append(badge)

        edit_btn = Gtk.Button(label=tr("Edit"))
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
            heading=tr("Delete Template?"),
            body=tr(
                "Are you sure you want to delete '{name}'?"
                " This action cannot be undone."
            ).format(name=name),
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
