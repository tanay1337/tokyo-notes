"""Spell check engine wrapping pyspellchecker with user dictionary support."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Set

from spellchecker import SpellChecker as PySpellChecker

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-zA-Z\u00C0-\u024F]+(?:['\u2019][a-zA-Z]+)?")


class SpellChecker:
    """Lightweight wrapper around pyspellchecker with user dict persistence."""

    def __init__(
        self, language: str = "en", user_dict_path: Path | None = None
    ) -> None:
        self._language = language
        self._user_dict_path = user_dict_path or (
            Path.home() / ".config" / "tokyo-notes" / "user_dict.txt"
        )
        self._ignore_session: Set[str] = set()
        self._spell: PySpellChecker | None = None
        self.load_dictionary(language)

    @property
    def language(self) -> str:
        return self._language

    @property
    def available_languages(self) -> list[str]:
        return list(PySpellChecker.languages())

    def load_dictionary(self, language: str) -> None:
        self._language = language
        self._spell = PySpellChecker(language=language)
        self._load_user_dict()
        logger.info("Spell checker loaded language=%s", language)

    def _load_user_dict(self) -> None:
        if self._spell is None:
            return
        path = self._user_dict_path
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                word = line.strip()
                if word:
                    self._spell.word_frequency.add(word)
        except OSError as exc:
            logger.warning("Could not read user dict %s: %s", path, exc)

    def check(self, word: str) -> bool:
        if self._spell is None:
            return True
        if word in self._ignore_session:
            return True
        return word in self._spell

    def suggest(self, word: str) -> list[str]:
        sp = self._spell
        if sp is None:
            return []
        candidates = sp.candidates(word)
        if not candidates:
            return []
        ranked = sorted(
            candidates,
            key=lambda w: sp.word_usage_frequency(w),
            reverse=True,
        )
        return ranked[:6]

    def add_to_user_dict(self, word: str) -> None:
        if self._spell is not None:
            self._spell.word_frequency.add(word)
        try:
            self._user_dict_path.parent.mkdir(parents=True, exist_ok=True)
            words: set[str] = set()
            if self._user_dict_path.exists():
                raw = self._user_dict_path.read_text(encoding="utf-8")
                words = {line.strip() for line in raw.splitlines() if line.strip()}
            words.add(word)
            self._user_dict_path.write_text(
                "\n".join(sorted(words)) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(
                "Could not write user dict %s: %s", self._user_dict_path, exc
            )

    def ignore_word(self, word: str) -> None:
        self._ignore_session.add(word)

    @staticmethod
    def extract_word(text: str, offset: int) -> tuple[str, int, int] | None:
        """Return (word, start_offset, end_offset) of the word containing offset."""
        match = _WORD_RE.search(text, offset)
        if not match:
            return None
        word_start, word_end = match.span()
        if word_start <= offset < word_end:
            return match.group(), word_start, word_end
        match = _WORD_RE.search(text, max(0, offset - 1))
        if match:
            word_start, word_end = match.span()
            if word_start <= offset <= word_end:
                return match.group(), word_start, word_end
        return None

    def all_known_words(self, words: list[str]) -> set[str]:
        if self._spell is None:
            return set(words)
        return self._spell.known(words)

    def destroy(self) -> None:
        self._spell = None
        self._ignore_session.clear()
