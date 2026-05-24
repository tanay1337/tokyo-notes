"""Toolbar widget construction for Tokyo Notes editor."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Gtk

# Groups: (prefix, suffix, tooltip, icon_file)
# A None entry inserts a visual separator between groups.
_GROUPS: list[list[tuple[str, str, str, str]] | None] = [
    # Headings
    [
        ("# ",   "", "Heading 1 (H1)", "h1.svg"),
        ("## ",  "", "Heading 2 (H2)", "h2.svg"),
        ("### ", "", "Heading 3 (H3)", "h3.svg"),
    ],
    None,
    # Inline styles
    [
        ("**",  "**",  "Bold",      "bold.svg"),
        ("_",   "_",   "Italic",     "italic.svg"),
        ("~~",  "~~",  "Strikethrough",        "strikethrough.svg"),
        ("`",   "`",   "Inline code",          "code.svg"),
    ],
    None,
    # Block elements
    [
        ("```\n", "\n```", "Code block",      "block.svg"),
        ("> ",    "",      "Block quote",     "quote.svg"),
        ("- ",    "",      "Bullet list",     "list.svg"),
        ("- [ ] ","",      "Task / checkbox", "checkbox.svg"),
    ],
    None,
    # Links & media
    [
        ("[Link](url)", "", "Insert link",  "link.svg"),
        ("![Alt](url)", "", "Insert image", "image.svg"),
    ],
]


def build_toolbar(assets_dir: Path, on_format: Any) -> Gtk.Box:
    """Build and return the editor toolbar widget with grouped buttons."""
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
    toolbar.add_css_class("toolbar")

    for group in _GROUPS:
        if group is None:
            # Visual separator between groups.
            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep.set_margin_start(4)
            sep.set_margin_end(4)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            toolbar.append(sep)
            continue

        for prefix, suffix, tooltip, icon_file in group:
            btn = Gtk.Button()
            btn.set_tooltip_text(tooltip)
            btn.add_css_class("toolbar-btn")
            icon_path = assets_dir / icon_file
            if icon_path.exists():
                img = Gtk.Image.new_from_file(str(icon_path))
                img.set_pixel_size(16)
                btn.set_child(img)
            else:
                # Fallback: short label derived from tooltip.
                btn.set_label(tooltip.split(" ")[0])
            btn.connect("clicked", on_format, prefix, suffix)
            toolbar.append(btn)

    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    toolbar.append(spacer)

    return toolbar
