"""Toolbar widget construction for Tokyo Notes editor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from gi.repository import Gtk

from core.translations import tr

# Sentinel values for special handler buttons in _GROUPS.
_FLASHCARD = object()
_DIAGRAM = object()
_TABLE = object()

# Groups: (prefix, suffix, tooltip, icon_file)
# A None entry inserts a visual separator between groups.
# Use a sentinel object (e.g. _FLASHCARD) as the prefix value for buttons
# that need a dedicated handler instead of the generic on_format callback.
_GROUPS: list[list[tuple[Any, str, str, str]] | None] = [
    # Headings
    [
        ("# ", "", "Heading 1 (H1)", "h1.svg"),
        ("## ", "", "Heading 2 (H2)", "h2.svg"),
        ("### ", "", "Heading 3 (H3)", "h3.svg"),
    ],
    None,
    # Inline styles
    [
        ("**", "**", "Bold", "bold.svg"),
        ("_", "_", "Italic", "italic.svg"),
        ("~~", "~~", "Strikethrough", "strikethrough.svg"),
        ("`", "`", "Inline Code", "code.svg"),
    ],
    None,
    # Block elements
    [
        ("```\n", "\n```", "Code Block", "block.svg"),
        ("> ", "", "Block Quote", "quote.svg"),
        ("- ", "", "Bullet List", "list.svg"),
        ("- [ ] ", "", "Task / Checkbox", "checkbox.svg"),
        (_FLASHCARD, "", "Insert flashcard", "flashcard.svg"),
        (_TABLE, "", "Insert table", "table.svg"),
    ],
    None,
    # Links & media
    [
        ("[Link](url)", "", "Insert link", "link.svg"),
        ("![Alt](url)", "", "Insert image", "image.svg"),
        (_DIAGRAM, "", "Insert diagram", "diagram.svg"),
    ],
]


def build_toolbar(
    assets_dir: Path,
    on_format: Any,
    on_history: Callable[[], Any] | None = None,
    get_buffer: Callable[[], Any] | None = None,
    speech_language: str | None = None,
    speech_input_device: str | int | None = None,
    on_speech_recording: Callable[[bool], None] | None = None,
    on_speech_transcribing: Callable[[bool], None] | None = None,
    on_speech_quiet_audio: Callable[[], None] | None = None,
) -> Gtk.ScrolledWindow:
    """Build and return the editor toolbar widget with grouped buttons.

    Returns a Gtk.ScrolledWindow wrapping the button bar so it scrolls
    horizontally on narrow windows.
    """
    inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
    inner.add_css_class("toolbar")
    inner._tag_buttons: dict[str, Gtk.Button] = {}

    # Tooltip → tag-name mapping for formatting-state buttons
    _tag_btn_map = {
        "Bold": "bold",
        "Italic": "italic",
        "Strikethrough": "strikethrough",
        "Inline Code": "code",
        "Heading 1 (H1)": "h1",
        "Heading 2 (H2)": "h2",
        "Heading 3 (H3)": "h3",
    }

    for group in _GROUPS:
        if group is None:
            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep.set_margin_start(4)
            sep.set_margin_end(4)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            inner.append(sep)
            continue

        for prefix, suffix, tooltip, icon_file in group:
            btn = Gtk.Button()
            btn.set_tooltip_text(tr(tooltip))
            btn.add_css_class("toolbar-btn")
            tag_name = _tag_btn_map.get(tooltip)
            if tag_name:
                btn._tag_name = tag_name
                inner._tag_buttons[tag_name] = btn
            icon_path = assets_dir / icon_file
            if icon_path.exists():
                img = Gtk.Image.new_from_file(str(icon_path))
                img.set_pixel_size(16)
                btn.set_child(img)
            else:
                btn.set_label(tr(tooltip.split(" ")[0]))
            if prefix is _FLASHCARD:
                btn.connect("clicked", on_format, prefix, "")
            else:
                btn.connect("clicked", on_format, prefix, suffix)
            inner.append(btn)

    scrolled = Gtk.ScrolledWindow()
    scrolled.add_css_class("toolbar-scrolled")
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scrolled.set_vexpand(False)

    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    inner.append(spacer)

    if get_buffer is not None:
        from ui.speech_button import SpeechButton

        mic_btn = SpeechButton(
            assets_dir=assets_dir,
            get_buffer=get_buffer,
            language=speech_language,
            input_device=speech_input_device,
            on_recording=on_speech_recording,
            on_transcribing=on_speech_transcribing,
            on_quiet_audio=on_speech_quiet_audio,
        )
        inner.append(mic_btn)
        scrolled._speech_btn = mic_btn

    if on_history:
        history_btn = Gtk.Button()
        history_btn.set_tooltip_text(tr("View version history"))
        history_btn.add_css_class("toolbar-btn")
        hist_path = assets_dir.parent / "toolbar" / "history.svg"
        if hist_path.exists():
            hist_img = Gtk.Image.new_from_file(str(hist_path))
            hist_img.set_pixel_size(16)
            history_btn.set_child(hist_img)
        else:
            history_btn.set_label(tr("Hist"))
        history_btn.connect("clicked", lambda _: on_history())
        history_btn.set_visible(False)
        inner.append(history_btn)
        scrolled._history_btn = history_btn

    scrolled.set_child(inner)

    return scrolled
