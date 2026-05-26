"""Tests for the flashcard parser (core/flashcard.py)."""

from __future__ import annotations

from core.flashcard import parse_note


def test_empty_content() -> None:
    assert parse_note("", "test") == []


def test_no_flashcard_blocks() -> None:
    content = "# My Note\n\nSome regular text.\n"
    assert parse_note(content, "test") == []


def test_single_flashcard() -> None:
    content = """```flashcard
What is the capital of France?
---
Paris
```"""
    cards = parse_note(content, "test")
    assert len(cards) == 1
    assert cards[0].question == "What is the capital of France?"
    assert cards[0].answer == "Paris"
    assert cards[0].note_path == "test"


def test_multiple_flashcards() -> None:
    content = """```flashcard
Q1?
---
A1
```

Some text in between.

```flashcard
Q2?
---
A2
```"""
    cards = parse_note(content, "test")
    assert len(cards) == 2
    assert cards[0].question == "Q1?"
    assert cards[0].answer == "A1"
    assert cards[1].question == "Q2?"
    assert cards[1].answer == "A2"


def test_multi_line_answer() -> None:
    content = """```flashcard
What is Python?
---
A high-level, interpreted programming language.
It is known for its readability.
```"""
    cards = parse_note(content, "test")
    assert len(cards) == 1
    assert cards[0].question == "What is Python?"
    assert "high-level" in cards[0].answer
    assert "readability" in cards[0].answer


def test_no_separator() -> None:
    content = """```flashcard
Just a single line
```"""
    assert parse_note(content, "test") == []


def test_empty_question() -> None:
    content = """```flashcard

---
Answer
```"""
    assert parse_note(content, "test") == []


def test_empty_answer() -> None:
    content = """```flashcard
Question
---
```"""
    assert parse_note(content, "test") == []


def test_only_flashcard_blocks_are_parsed() -> None:
    content = """```python
print("hello")
```

```flashcard
Question?
---
Answer.
```"""
    cards = parse_note(content, "test")
    assert len(cards) == 1
    assert cards[0].question == "Question?"


def test_extra_whitespace() -> None:
    content = """```flashcard

  Question with spaces

---

  Answer with spaces

```"""
    cards = parse_note(content, "test")
    assert len(cards) == 1
    assert cards[0].question == "Question with spaces"
    assert cards[0].answer == "Answer with spaces"


def test_note_path_preserved() -> None:
    content = """```flashcard
Q?
---
A
```"""
    cards = parse_note(content, "My Great Note")
    assert cards[0].note_path == "My Great Note"
