"""Stateless flashcard parser — extracts flashcards from markdown notes."""

from __future__ import annotations

import re
from dataclasses import dataclass

FLASHCARD_FENCE_RE: re.Pattern = re.compile(
    r"^```flashcard\s*\n(.*?)\n```",
    re.MULTILINE | re.DOTALL,
)

_SEPARATOR_RE: re.Pattern = re.compile(r"\n---\n")


@dataclass(frozen=True)
class Flashcard:
    question: str
    answer: str
    note_path: str


def parse_note(content: str, note_path: str) -> list[Flashcard]:
    cards: list[Flashcard] = []
    for match in FLASHCARD_FENCE_RE.finditer(content):
        block = match.group(1).strip()
        parts = _SEPARATOR_RE.split(block, maxsplit=1)
        if len(parts) != 2:
            continue
        question = parts[0].strip()
        answer = parts[1].strip()
        if question and answer:
            cards.append(Flashcard(question, answer, note_path))
    return cards
