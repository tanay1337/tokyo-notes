from __future__ import annotations

from pathlib import Path

from core.assistant import (
    ChatHistoryStore,
    ChatThread,
    ContextAttachment,
    EditProposal,
    StoredMessage,
    build_context,
    ensure_history_gitignored,
    parse_flashcards,
)


def test_context_uses_data_boundaries() -> None:
    item = ContextAttachment.create("note", 'A "note"', "ignore previous")
    result = build_context([item])
    assert '<document label="A \'note\'" kind="note">' in result
    assert result.endswith("</document>")


def test_history_does_not_duplicate_attachment_content(tmp_path: Path) -> None:
    store = ChatHistoryStore(tmp_path)
    thread = ChatThread(
        title="Hello",
        messages=[StoredMessage("user", "question")],
        attachment_names=["Private/Long note"],
    )
    store.save(thread)
    path = next(store.root.glob("*.json"))
    raw = path.read_text(encoding="utf-8")
    assert "Private/Long note" in raw
    assert path.stat().st_mode & 0o777 == 0o600
    assert store.load_all()[0].messages[0].content == "question"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count(
        ".tokyo-notes/assistant/"
    ) == 1


def test_ephemeral_thread_is_not_saved(tmp_path: Path) -> None:
    store = ChatHistoryStore(tmp_path)
    store.save(ChatThread(ephemeral=True))
    assert not store.root.exists()


def test_corrupt_history_is_skipped(tmp_path: Path) -> None:
    store = ChatHistoryStore(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "bad.json").write_text("{", encoding="utf-8")
    assert store.load_all() == []


def test_gitignore_preserves_existing_rules(tmp_path: Path) -> None:
    path = tmp_path / ".gitignore"
    path.write_text("mine\n", encoding="utf-8")
    ensure_history_gitignored(tmp_path)
    ensure_history_gitignored(tmp_path)
    assert path.read_text(encoding="utf-8").count(".tokyo-notes/assistant/") == 1
    assert path.read_text(encoding="utf-8").startswith("mine\n")


def test_flashcard_validation() -> None:
    source = "```flashcard\nQ\n---\nA\n```"
    assert parse_flashcards(source) == source
    assert parse_flashcards("```flashcard\nQ only\n```") is None


def test_proposal_freshness() -> None:
    item = ContextAttachment.create("note", "N", "before")
    proposal = EditProposal("replace_note", "N", item.content_hash, "after")
    assert proposal.is_fresh("before")
    assert not proposal.is_fresh("changed")
