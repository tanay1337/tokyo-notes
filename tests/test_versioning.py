"""Tests for core/versioning.py — GitVersionController."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.versioning as versioning_module
from core.versioning import AutoCommitScheduler, GitVersionController


@pytest.fixture
def notes_dir() -> Iterator[Path]:
    """Create a temporary directory to serve as the notes folder."""
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def git_controller(notes_dir: Path) -> GitVersionController:
    """Create a GitVersionController backed by a temporary directory."""
    gc = GitVersionController(str(notes_dir))
    if not gc.is_git_installed():
        pytest.skip("git is not installed on this system")
    gc.init_repo()
    return gc


class TestGitVersionController:
    def test_is_git_installed(self) -> None:
        installed = shutil.which("git") is not None
        assert GitVersionController.is_git_installed() == installed


class TestAutoCommitScheduler:
    def test_repeated_saves_create_one_thirty_second_batch(self, monkeypatch) -> None:
        callbacks = []
        removed = []
        submitted = []
        controller = MagicMock()
        controller.is_available.return_value = True
        monkeypatch.setattr(
            versioning_module.GLib,
            "timeout_add",
            lambda delay, callback: (callbacks.append((delay, callback)), 41)[1],
        )
        monkeypatch.setattr(
            versioning_module.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        scheduler = AutoCommitScheduler(controller, submitted.append)

        for _ in range(8):
            scheduler.mark_dirty("Sample Note")

        assert len(callbacks) == 1
        assert callbacks[0][0] == 30_000
        assert submitted == []

        callbacks[0][1]()
        assert len(submitted) == 1
        submitted[0]()
        controller.auto_commit.assert_called_once_with("Sample Note")
        assert scheduler.pending_notes == set()
        assert removed == []  # the active timeout already fired

    def test_note_exit_flushes_immediately(self, monkeypatch) -> None:
        submitted = []
        removed = []
        controller = MagicMock()
        monkeypatch.setattr(versioning_module.GLib, "timeout_add", lambda *_: 7)
        monkeypatch.setattr(
            versioning_module.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        scheduler = AutoCommitScheduler(controller, submitted.append)
        scheduler.mark_dirty("Sample Note")

        scheduler.flush_note("Sample Note")

        assert len(submitted) == 1
        assert removed == [7]

    def test_rename_ancestry_is_preserved_across_batch(self, monkeypatch) -> None:
        submitted = []
        controller = MagicMock()
        monkeypatch.setattr(versioning_module.GLib, "timeout_add", lambda *_: 8)
        monkeypatch.setattr(versioning_module.GLib, "source_remove", lambda *_: None)
        scheduler = AutoCommitScheduler(controller, submitted.append)
        scheduler.mark_dirty("New", "Old")
        scheduler.mark_dirty("Newest", "New")

        scheduler.flush_all()
        submitted[0]()

        controller.rename_note.assert_called_once_with("Old", "Newest")
        controller.auto_commit.assert_called_once_with("Newest")


class TestGitRepositoryOperations:
    def test_init_repo_creates_git_dir(self, notes_dir: Path) -> None:
        gc = GitVersionController(str(notes_dir))
        assert not gc.is_repo()
        result = gc.init_repo()
        assert result is True
        assert (notes_dir / ".git").is_dir()
        assert gc.is_repo()

    def test_init_repo_idempotent(self, git_controller) -> None:
        assert git_controller.is_repo()
        result = git_controller.init_repo()
        assert result is True

    def test_init_repo_creates_gitignore(self, notes_dir: Path) -> None:
        gc = GitVersionController(str(notes_dir))
        gc.init_repo()
        assert (notes_dir / ".gitignore").exists()
        content = (notes_dir / ".gitignore").read_text()
        assert "*.tmp" in content

    def test_auto_commit_creates_commit(self, git_controller, notes_dir: Path) -> None:
        note_path = notes_dir / "test.md"
        note_path.write_text("# Test Note\n\nHello world\n", encoding="utf-8")
        result = git_controller.auto_commit("test")
        assert result is True
        commits = git_controller.history("test", max_count=10)
        assert len(commits) >= 1
        assert "test" in commits[0].message

    def test_auto_commit_skips_if_no_changes(
        self, git_controller, notes_dir: Path
    ) -> None:
        note_path = notes_dir / "test.md"
        note_path.write_text("# Test\n", encoding="utf-8")
        git_controller.auto_commit("test")
        result = git_controller.auto_commit("test")
        assert result is False

    def test_auto_commit_detects_content_changes(
        self, git_controller, notes_dir: Path
    ) -> None:
        note_path = notes_dir / "test.md"
        note_path.write_text("# Version 1\n", encoding="utf-8")
        git_controller.auto_commit("test")
        note_path.write_text("# Version 2\n\nUpdated content\n", encoding="utf-8")
        result = git_controller.auto_commit("test")
        assert result is True
        commits = git_controller.history("test", max_count=10)
        assert len(commits) >= 2

    def test_history_returns_commits_in_order(
        self, git_controller, notes_dir: Path
    ) -> None:
        note_path = notes_dir / "test.md"
        for i in range(3):
            note_path.write_text(f"# Version {i}\n", encoding="utf-8")
            git_controller.auto_commit("test")
        commits = git_controller.history("test", max_count=10)
        assert len(commits) >= 3
        timestamps = [c.timestamp for c in commits[:3]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_diff_returns_content(self, git_controller, notes_dir: Path) -> None:
        note_path = notes_dir / "test.md"
        note_path.write_text("# First\n", encoding="utf-8")
        git_controller.auto_commit("test")
        note_path.write_text("# Second\n\nMore content\n", encoding="utf-8")
        git_controller.auto_commit("test")
        commits = git_controller.history("test", max_count=2)
        assert len(commits) >= 2
        diff = git_controller.diff(commits[0].hexsha, "test")
        assert isinstance(diff, str)
        assert len(diff) > 0

    def test_restore_returns_previous_content(
        self, git_controller, notes_dir: Path
    ) -> None:
        note_path = notes_dir / "test.md"
        note_path.write_text("# Original content\n", encoding="utf-8")
        git_controller.auto_commit("test")
        first_commit = git_controller.history("test", max_count=1)[0]
        note_path.write_text("# Overwritten content\n", encoding="utf-8")
        git_controller.auto_commit("test")
        restored = git_controller.restore(first_commit.hexsha, "test")
        assert restored is not None
        assert "Original" in restored

    def test_commit_deletion(self, git_controller, notes_dir: Path) -> None:
        note_path = notes_dir / "test.md"
        note_path.write_text("# To delete\n", encoding="utf-8")
        git_controller.auto_commit("test")
        note_path.unlink()
        result = git_controller.commit_deletion("test")
        assert result is True
        commits = git_controller.history("test", max_count=10)
        assert len(commits) > 0

    def test_snapshot_commits_all(self, git_controller, notes_dir: Path) -> None:
        note1 = notes_dir / "a.md"
        note2 = notes_dir / "b.md"
        note1.write_text("# A\n", encoding="utf-8")
        note2.write_text("# B\n", encoding="utf-8")
        result = git_controller.snapshot("bulk add")
        assert result is True
        commits_a = git_controller.history("a", max_count=10)
        commits_b = git_controller.history("b", max_count=10)
        assert len(commits_a) >= 1
        assert len(commits_b) >= 1

    def test_snapshot_skips_if_no_changes(self, git_controller) -> None:
        result = git_controller.snapshot("no changes")
        assert result is False

    def test_rename_tracks_in_git(self, git_controller, notes_dir: Path) -> None:
        note_path = notes_dir / "old.md"
        note_path.write_text("# Rename me\n", encoding="utf-8")
        git_controller.auto_commit("old")
        note_path.rename(notes_dir / "new.md")
        git_controller.rename_note("old", "new")
        git_controller.auto_commit("new")
        assert not (notes_dir / "old.md").exists()
        history_new = git_controller.history("new", max_count=10)
        assert len(history_new) >= 1

    def test_encrypted_file(self, git_controller, notes_dir: Path) -> None:
        note_path = notes_dir / "secret.md.enc"
        note_path.write_bytes(b"some encrypted binary data here")
        result = git_controller.auto_commit("secret")
        assert result is True
        commits = git_controller.history("secret", max_count=10)
        assert len(commits) >= 1

    def test_no_history_for_nonexistent_file(
        self,
        git_controller,
    ) -> None:
        commits = git_controller.history("nonexistent_note", max_count=10)
        assert commits == []

    def test_history_empty_for_uncommitted_file(
        self, git_controller, notes_dir: Path
    ) -> None:
        note_path = notes_dir / "uncommitted.md"
        note_path.write_text("# Not committed\n", encoding="utf-8")
        commits = git_controller.history("uncommitted", max_count=10)
        assert commits == []

    def test_is_available(self, git_controller) -> None:
        assert git_controller.is_available() is True

    def test_not_available_without_repo(self, notes_dir: Path) -> None:
        gc = GitVersionController(str(notes_dir))
        assert gc.is_available() is False
