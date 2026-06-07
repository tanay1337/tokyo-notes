"""Translation / i18n support — English-as-key JSON lookup."""

from __future__ import annotations

import json
from pathlib import Path

_TRANSLATIONS: dict[str, str] = {}


def load(lang: str, translations_dir: Path | None = None) -> None:
    """Load translations for *lang* (e.g. ``"de"``, ``"ja"``).

    Falls back to English (built-in) if the file is missing.
    """
    global _TRANSLATIONS
    if translations_dir is None:
        translations_dir = Path(__file__).resolve().parent.parent / "translations"
    lang_file = translations_dir / f"{lang}.json"
    if lang_file.exists():
        with open(lang_file, encoding="utf-8") as f:
            _TRANSLATIONS = json.load(f)
    else:
        _TRANSLATIONS = {}


def tr(text: str) -> str:
    """Translate *text* (an English source string) to the current language.

    Returns the original English string when no translation exists.
    """
    return _TRANSLATIONS.get(text, text)


def tr_n(singular: str, plural: str, count: int) -> str:
    """Translate with pluralisation support.

    ``tr_n("1 note", "{n} notes", count)`` returns the translated
    string that matches *count*, with ``{n}`` substituted.
    """
    key = plural if count != 1 else singular
    return _TRANSLATIONS.get(key, key).format(n=count)


def list_languages(translations_dir: Path | None = None) -> dict[str, str]:
    """Return available languages as ``{code: display_name}``."""
    if translations_dir is None:
        translations_dir = Path(__file__).resolve().parent.parent / "translations"
    languages: dict[str, str] = {}
    for fpath in sorted(translations_dir.glob("*.json")):
        code = fpath.stem
        try:
            with open(fpath, encoding="utf-8") as f:
                meta = json.load(f).get("_meta", {})
            languages[code] = meta.get("name", code)
        except json.JSONDecodeError:
            languages[code] = code
    languages.setdefault("en", "English")
    return languages
