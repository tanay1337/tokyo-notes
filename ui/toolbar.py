# ui/toolbar.py
from pathlib import Path
from gi.repository import Gtk

FORMATS = [
    ("**",   "**",   "Bold",          "bold.svg"),
    ("_",    "_",    "Italic",        "italic.svg"),
    ("~~",   "~~",   "Strikethrough", "strikethrough.svg"),
    ("# ",   "",     "H1",            "h1.svg"),
    ("## ",  "",     "H2",            "h2.svg"),
    ("### ", "",     "H3",            "h3.svg"),
    ("`",    "`",    "Code",          "code.svg"),
    ("```\n","\n```","Block",         "block.svg"),
    ("- ",   "",     "List",          "list.svg"),
    ("- [ ] ","",    "Checkbox",      "checkbox.svg"),
    ("[Link](url)", "", "Link",       "link.svg"),
    ("![Alt](url)", "", "Image",      "image.svg"),
    ("> ",   "",     "Quote",         "quote.svg"),
]

def build_toolbar(assets_dir: Path, on_format) -> Gtk.Box:
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    toolbar.add_css_class("toolbar")
    for prefix, suffix, label, icon_file in FORMATS:
        btn = Gtk.Button()
        btn.set_tooltip_text(label)
        btn.add_css_class("toolbar-btn")
        icon_path = assets_dir / icon_file
        if icon_path.exists():
            img = Gtk.Image.new_from_file(str(icon_path))
            img.set_pixel_size(16)
            btn.set_child(img)
        else:
            btn.set_label(label)
        btn.connect("clicked", on_format, prefix, suffix)
        toolbar.append(btn)
    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    toolbar.append(spacer)
    return toolbar
