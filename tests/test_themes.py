"""Tests that all theme CSS files have the required @define-color variables."""

from __future__ import annotations

import re
from pathlib import Path

_THEMES_DIR = Path(__file__).resolve().parent.parent / "themes"

_CORE_VARS = frozenset(
    {
        "bg_color",
        "sidebar_bg",
        "fg_color",
        "selected_fg_color",
        "accent_color",
        "selection_color",
        "header_bg",
        "border_color",
        "deadline_color",
        "editor_bg",
        "card_bg",
        "input_bg",
        "input_border",
        "hover_bg",
        "active_bg",
        "secondary_fg",
        "scrollbar_bg",
        "scrollbar_fg",
    }
)

_SYNTAX_VARS = frozenset(
    {
        "syntax_h1",
        "syntax_h2",
        "syntax_h3",
        "syntax_h4",
        "syntax_h5",
        "syntax_h6",
        "syntax_code_bg",
        "syntax_code_fg",
        "syntax_code_block_bg",
        "syntax_code_block_fg",
        "syntax_checkbox_empty",
        "syntax_checkbox_checked",
        "syntax_internal_link",
        "syntax_external_link",
        "syntax_image",
        "syntax_tag",
        "syntax_deadline",
        "syntax_hr",
        "syntax_bullet",
        "syntax_number",
        "syntax_table",
        "syntax_blockquote",
        "syntax_dim",
    }
)

_ALL_REQUIRED = _CORE_VARS | _SYNTAX_VARS

_VAR_RE = re.compile(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]+)\s*;")


def _parse_vars(css: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _VAR_RE.finditer(css)}


def theme_files() -> list[Path]:
    return sorted(_THEMES_DIR.glob("*.css"))


class TestThemeCompleteness:
    def test_all_themes_have_required_vars(self):
        for css_path in theme_files():
            css = css_path.read_text(encoding="utf-8")
            vars_ = _parse_vars(css)
            missing = _ALL_REQUIRED - vars_.keys()
            assert not missing, (
                f"{css_path.name} is missing {len(missing)} required"
                f" variable(s): {', '.join(sorted(missing))}"
            )

    def test_no_extra_vars(self):
        for css_path in theme_files():
            css = css_path.read_text(encoding="utf-8")
            vars_ = _parse_vars(css)
            extra = vars_.keys() - _ALL_REQUIRED
            assert not extra, (
                f"{css_path.name} has {len(extra)} unexpected"
                f" variable(s): {', '.join(sorted(extra))}"
            )

    def test_all_theme_files_exist(self):
        """Every entry in THEMES has a matching .css file."""
        from core.theme_manager import THEMES

        for t in THEMES:
            css_path = _THEMES_DIR / f"{t['id']}.css"
            assert css_path.exists(), (
                f"Theme '{t['id']}' registered in THEMES but {css_path.name} not found"
            )
