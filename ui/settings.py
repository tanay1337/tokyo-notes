import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

class SettingsView(Gtk.Box):
    def __init__(self, on_theme_selected, on_config_changed, on_select_folder_callback, config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("dashboard-view")
        self.on_theme_selected = on_theme_selected
        self.on_config_changed = on_config_changed
        self.on_select_folder_callback = on_select_folder_callback
        self.config = config
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        # General Section
        general_section_label = Gtk.Label(label="General")
        general_section_label.add_css_class("dashboard-header")
        general_section_label.set_halign(Gtk.Align.START)
        general_section_label.set_margin_start(10)
        general_section_label.set_margin_top(15)
        content.append(general_section_label)

        general_list = Gtk.ListBox()
        general_list.set_selection_mode(Gtk.SelectionMode.NONE)
        general_list.add_css_class("settings-list")
        general_list.set_margin_start(15)
        general_list.set_margin_end(15)
        general_list.set_margin_top(10)

        # Folder Selection Row
        folder_row = Gtk.ListBoxRow()
        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        folder_box.set_margin_start(15)
        folder_box.set_margin_end(15)
        folder_box.set_margin_top(10)
        folder_box.set_margin_bottom(10)
        
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        
        title_label = Gtk.Label(label="Notes Folder", xalign=0)
        title_label.add_css_class("theme-name")
        text_box.append(title_label)
        
        path_label = Gtk.Label(label=self.config.get('notes_folder', "Not set"), xalign=0)
        path_label.add_css_class("theme-preview")
        text_box.append(path_label)
        self.path_label = path_label
        
        folder_box.append(text_box)
        
        folder_btn = Gtk.Button(label="Select")
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.on_select_folder_clicked)
        folder_box.append(folder_btn)
        
        folder_row.set_child(folder_box)
        general_list.append(folder_row)
        content.append(general_list)

        # Toolbars Section
        toolbar_section_label = Gtk.Label(label="Toolbars")
        toolbar_section_label.add_css_class("dashboard-header")
        toolbar_section_label.set_halign(Gtk.Align.START)
        toolbar_section_label.set_margin_start(10)
        toolbar_section_label.set_margin_top(25)
        content.append(toolbar_section_label)

        toolbars_list = Gtk.ListBox()
        toolbars_list.set_selection_mode(Gtk.SelectionMode.NONE)
        toolbars_list.add_css_class("settings-list")
        toolbars_list.set_margin_start(15)
        toolbars_list.set_margin_end(15)
        toolbars_list.set_margin_top(10)

        # Formatting Bar Toggle
        formatting_row = self.create_toggle_row(
            "Formatting Bar", 
            "Show markdown formatting tools above the editor",
            'show_toolbar'
        )
        toolbars_list.append(formatting_row)

        # Status Bar Toggle
        status_row = self.create_toggle_row(
            "Status Bar", 
            "Show word count and reading time at the bottom",
            'show_stats'
        )
        toolbars_list.append(status_row)

        content.append(toolbars_list)

        # Theme Section
        theme_section_label = Gtk.Label(label="Theme")
        theme_section_label.add_css_class("dashboard-header")
        theme_section_label.set_halign(Gtk.Align.START)
        theme_section_label.set_margin_start(10)
        theme_section_label.set_margin_top(25)
        content.append(theme_section_label)

        self.theme_list = Gtk.ListBox()
        self.theme_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.theme_list.add_css_class("settings-list")
        
        self.themes = [
            {"id": "tokyo-night", "name": "Tokyo Night", "preview": "Deep blues and vibrant accents"},
            {"id": "cyberpunk-2077", "name": "Cyberpunk 2077", "preview": "Night City vibes: Yellow, Cyan, and Black"},
            {"id": "nord", "name": "Nord", "preview": "Arctic blue, clean and elegant"},
            {"id": "gruvbox", "name": "Gruvbox", "preview": "Retro warm tones, easy on the eyes"},
            {"id": "dracula", "name": "Dracula", "preview": "High contrast, vibrant purple tones"}
        ]
        
        self.theme_rows = {}
        current_theme = config.get('theme', 'tokyo-night')
        for theme in self.themes:
            row = self.create_theme_row(theme, theme["id"] == current_theme)
            self.theme_list.append(row)
            self.theme_rows[theme["id"]] = row
        
        content.append(self.theme_list)
            
        scrolled.set_child(content)
        self.append(scrolled)

    def create_theme_row(self, theme, is_active):
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

    def create_toggle_row(self, title, subtitle, config_key):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("theme-name")
        text_box.append(title_label)
        
        subtitle_label = Gtk.Label(label=subtitle, xalign=0)
        subtitle_label.add_css_class("theme-preview")
        text_box.append(subtitle_label)
        
        box.append(text_box)
        
        switch = Gtk.Switch()
        switch.set_active(self.config.get(config_key, True))
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("state-set", self.on_toggle_changed, config_key)
        box.append(switch)
        
        row.set_child(box)
        return row

    def on_toggle_changed(self, switch, state, config_key):
        self.on_config_changed(config_key, state)
        return False

    def update_folder_path(self, new_path):
        self.path_label.set_label(new_path)

    def on_select_folder_clicked(self, button):
        self.on_select_folder_callback(button)

    def select_theme(self, theme_id):
        # Update UI
        for tid, row in self.theme_rows.items():
            card = row.get_child()
            if tid == theme_id:
                card.add_css_class("active")
            else:
                card.remove_css_class("active")
        
        # Trigger callback
        self.on_theme_selected(theme_id)
