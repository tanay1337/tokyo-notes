"""Tests for core/storage.py — NotesManager disk I/O and caching."""

from __future__ import annotations

import pytest

from core.storage import NotesManager


@pytest.fixture
def nm(tmp_path):
    """Return a NotesManager backed by a temporary directory."""
    return NotesManager(notes_dir=tmp_path)


class TestNotesManager:
    def test_create_and_read(self, nm: NotesManager) -> None:
        nm.save_note("Test Note", "Hello world")
        assert nm.read_plain("Test Note") == "Hello world"

    def test_get_notes_empty(self, nm: NotesManager) -> None:
        assert nm.get_notes() == []

    def test_get_notes_after_create(self, nm: NotesManager) -> None:
        nm.save_note("Note A", "Content A")
        nm.save_note("Note B", "Content B")
        notes = nm.get_notes()
        assert "Note A" in notes
        assert "Note B" in notes

    def test_rename_note(self, nm: NotesManager) -> None:
        nm.save_note("Old Name", "Content")
        assert nm.rename_note("Old Name", "New Name")
        assert "Old Name" not in nm.get_notes()
        assert "New Name" in nm.get_notes()
        assert nm.read_plain("New Name") == "Content"

    def test_rename_nonexistent(self, nm: NotesManager) -> None:
        assert not nm.rename_note("Ghost", "Something")

    def test_delete_note(self, nm: NotesManager) -> None:
        nm.save_note("To Delete", "Content")
        nm.delete_note("To Delete")
        assert "To Delete" not in nm.get_notes()

    def test_delete_nonexistent(self, nm: NotesManager) -> None:
        nm.delete_note("Ghost")  # should not raise

    def test_is_encrypted(self, nm: NotesManager) -> None:
        nm.save_note("Plain", "Content")
        assert not nm.is_encrypted("Plain")

    def test_read_nonexistent(self, nm: NotesManager) -> None:
        assert nm.read_plain("Ghost") == ""

    def test_reserve_name_basic(self, nm: NotesManager) -> None:
        assert nm.reserve_name("Untitled") == "Untitled"

    def test_reserve_name_increments(self, nm: NotesManager) -> None:
        nm.save_note("Untitled", "")
        assert nm.reserve_name("Untitled") == "Untitled 1"

    def test_cache_serves_subsequent_read(self, nm: NotesManager) -> None:
        nm.save_note("Cached", "Data")
        nm.read_plain("Cached")  # populate cache
        # Second read should return cached data (no FileNotFoundError)
        assert nm.read_plain("Cached") == "Data"

    def test_encrypted_round_trip(self, nm: NotesManager) -> None:
        nm.save_encrypted("Secret", b"\x00\x01\x02\x03")
        assert nm.is_encrypted("Secret")
        # read_encrypted_raw returns the ciphertext stored
        raw = nm.read_encrypted_raw("Secret")
        assert raw == b"\x00\x01\x02\x03"

    def test_get_notes_excludes_temp_files(self, nm: NotesManager) -> None:
        nm.save_note("Real", "Content")
        # Write a dotfile that should be excluded
        (nm.notes_dir / ".swp").write_text("garbage")
        assert "Real" in nm.get_notes()
        assert ".swp" not in nm.get_notes()
        assert ".md" not in nm.get_notes()

    def test_get_metadata_plain(self, nm: NotesManager) -> None:
        nm.save_note("Meta", "Hello\n- [ ] task")
        meta = nm.get_metadata("Meta")
        assert not meta.get("encrypted", False)
        assert "checkboxes" in meta

    def test_get_metadata_encrypted(self, nm: NotesManager) -> None:
        nm.save_encrypted("Priv", b"\x00\x01")
        meta = nm.get_metadata("Priv")
        assert meta.get("encrypted", False) is True

    def test_get_all_checkboxes_empty(self, nm: NotesManager) -> None:
        assert nm.get_all_checkboxes() == []

    def test_get_all_checkboxes_with_data(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "Hello\n- [ ] buy milk @2025-01-01")
        boxes = nm.get_all_checkboxes()
        assert len(boxes) == 1
        assert boxes[0]["text"] == "buy milk"

    def test_get_all_checkboxes_exclude(self, nm: NotesManager) -> None:
        nm.save_note("A", "- [ ] aaa\n- [ ] bbb")
        nm.save_note("B", "- [ ] ccc")
        boxes = nm.get_all_checkboxes(exclude={"A"})
        assert len(boxes) == 1
        assert boxes[0]["text"] == "ccc"

    def test_stale_temp_files_cleaned_on_startup(self, tmp_path) -> None:
        (tmp_path / ".Old.tmp").write_text("stale", encoding="utf-8")
        (tmp_path / "Secret.md.enc.new").write_bytes(b"stale")

        NotesManager(notes_dir=tmp_path)

        assert not (tmp_path / ".Old.tmp").exists()
        assert not (tmp_path / "Secret.md.enc.new").exists()

    def test_failed_plain_save_removes_temp_file(self, nm: NotesManager, monkeypatch):
        import os

        def fail_replace(src, dst):
            if src.endswith(".tmp"):
                raise OSError("rename failed")
            return os.replace(src, dst)

        monkeypatch.setattr(os, "replace", fail_replace)

        with pytest.raises(OSError):
            nm.save_note("Broken", "content")

        # Verify that the temp file was removed
        tmp_files = list(nm.notes_dir.glob(".Broken-*.tmp"))
        assert len(tmp_files) == 0
        assert not (nm.notes_dir / "Broken.md").exists()


class TestValidateName:
    def test_valid_names(self) -> None:
        for name in ["Hello", "My Note", "note-1", "draft.md", "test (2024)"]:
            assert NotesManager.validate_name(name) == name

    def test_valid_folder_names(self) -> None:
        for name in ["Work/Hi", "Work/Month/Hi", "a/b/c/d"]:
            assert NotesManager.validate_name(name) == name

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError):
            NotesManager.validate_name("../../etc/passwd")

    def test_rejects_leading_slash(self) -> None:
        with pytest.raises(ValueError):
            NotesManager.validate_name("/etc/passwd")

    def test_rejects_trailing_slash(self) -> None:
        with pytest.raises(ValueError):
            NotesManager.validate_name("Work/")

    def test_rejects_double_slash(self) -> None:
        with pytest.raises(ValueError):
            NotesManager.validate_name("Work//Hi")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            NotesManager.validate_name("")

    def test_rejects_only_special(self) -> None:
        with pytest.raises(ValueError):
            NotesManager.validate_name("???")
        with pytest.raises(ValueError):
            NotesManager.validate_name("***")


class TestUpdateCheckbox:
    def test_toggle_on(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk")
        assert nm.update_checkbox("Tasks", 1, True)
        content = nm.read_plain("Tasks")
        assert "- [x] buy milk" in content

    def test_toggle_off(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [x] buy milk")
        assert nm.update_checkbox("Tasks", 1, False)
        content = nm.read_plain("Tasks")
        assert "- [ ] buy milk" in content

    def test_invalid_line_returns_false(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk")
        assert not nm.update_checkbox("Tasks", 99, True)

    def test_non_checkbox_line_returns_false(self, nm: NotesManager) -> None:
        nm.save_note("Notes", "just text")
        assert not nm.update_checkbox("Notes", 1, True)


class TestUpdateDeadline:
    def test_add_deadline(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk")
        assert nm.update_deadline("Tasks", 1, "2025-06-01")
        content = nm.read_plain("Tasks")
        assert "@2025-06-01" in content

    def test_add_deadline_with_time(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk")
        assert nm.update_deadline("Tasks", 1, "2025-06-01 14:30")
        content = nm.read_plain("Tasks")
        assert "@2025-06-01 14:30" in content

    def test_remove_deadline(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk @2025-06-01")
        assert nm.update_deadline("Tasks", 1, None)
        content = nm.read_plain("Tasks")
        assert "@2025-06-01" not in content

    def test_replace_deadline(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk @2025-06-01")
        assert nm.update_deadline("Tasks", 1, "2025-07-04")
        content = nm.read_plain("Tasks")
        assert "@2025-07-04" in content
        assert "@2025-06-01" not in content

    def test_invalid_line_returns_false(self, nm: NotesManager) -> None:
        nm.save_note("Tasks", "- [ ] buy milk @2025-06-01")
        assert not nm.update_deadline("Tasks", 99, "2025-07-01")


class TestFolderNotes:
    def test_save_and_read_folder_note(self, nm: NotesManager) -> None:
        nm.save_note("Work/Hi", "Hello world")
        assert nm.read_plain("Work/Hi") == "Hello world"
        assert (nm.notes_dir / "Work" / "Hi.md").exists()

    def test_save_creates_parent_dirs(self, nm: NotesManager) -> None:
        nm.save_note("a/b/c/d/e/note", "nested")
        assert nm.read_plain("a/b/c/d/e/note") == "nested"
        assert (nm.notes_dir / "a" / "b" / "c" / "d" / "e" / "note.md").exists()

    def test_get_notes_includes_folder_notes(self, nm: NotesManager) -> None:
        nm.save_note("Plain", "root")
        nm.save_note("Work/Hi", "folder note")
        nm.save_note("Work/Month/Deep", "deeply nested")
        notes = nm.get_notes()
        assert "Plain" in notes
        assert "Work/Hi" in notes
        assert "Work/Month/Deep" in notes

    def test_get_notes_search_finds_folder_notes(self, nm: NotesManager) -> None:
        nm.save_note("Work/Hi", "hello")
        result = nm.get_notes(search_text="Hi")
        assert "Work/Hi" in result
        # Search by folder path component
        result2 = nm.get_notes(search_text="Work")
        assert "Work/Hi" in result2

    def test_delete_folder_note_cleans_up_empty_dir(self, nm: NotesManager) -> None:
        nm.save_note("Work/Month/Hi", "content")
        nm.delete_note("Work/Month/Hi")
        assert "Work/Month/Hi" not in nm.get_notes()
        # Empty parent dirs should be cleaned up
        assert not (nm.notes_dir / "Work" / "Month").exists()
        assert not (nm.notes_dir / "Work").exists()

    def test_delete_keeps_non_empty_dir(self, nm: NotesManager) -> None:
        nm.save_note("Work/A", "note a")
        nm.save_note("Work/B", "note b")
        nm.delete_note("Work/A")
        assert (nm.notes_dir / "Work").exists()
        assert (nm.notes_dir / "Work" / "B.md").exists()

    def test_rename_moves_between_folders(self, nm: NotesManager) -> None:
        nm.save_note("Work/Hi", "content")
        assert nm.rename_note("Work/Hi", "Personal/Hi")
        assert "Work/Hi" not in nm.get_notes()
        assert "Personal/Hi" in nm.get_notes()
        assert nm.read_plain("Personal/Hi") == "content"

    def test_rename_within_folder(self, nm: NotesManager) -> None:
        nm.save_note("Work/Hi", "content")
        assert nm.rename_note("Work/Hi", "Work/Hello")
        assert nm.read_plain("Work/Hello") == "content"

    def test_rename_to_root(self, nm: NotesManager) -> None:
        nm.save_note("Work/Hi", "content")
        assert nm.rename_note("Work/Hi", "Hi")
        assert nm.read_plain("Hi") == "content"
        assert not (nm.notes_dir / "Work").exists()

    def test_is_encrypted_folder_note(self, nm: NotesManager) -> None:
        nm.save_encrypted("Work/Secret", b"\x00\x01")
        assert nm.is_encrypted("Work/Secret")

    def test_get_encrypted_notes_recursive(self, nm: NotesManager) -> None:
        nm.save_encrypted("RootSecret", b"\x00")
        nm.save_encrypted("Work/Secret", b"\x01")
        result = nm.get_encrypted_notes()
        assert "RootSecret" in result
        assert "Work/Secret" in result

    def test_get_folders_returns_folders(self, nm: NotesManager) -> None:
        nm.save_note("Work/Hi", "a")
        nm.save_note("Work/Month/Deep", "b")
        nm.save_note("Personal/Journal", "c")
        folders = nm.get_folders()
        assert "Work" in folders
        assert "Work/Month" in folders
        assert "Personal" in folders

    def test_get_folders_excludes_hidden(self, nm: NotesManager) -> None:
        nm.save_note("Plain", "root")
        (nm.notes_dir / ".templates" / "tmpl.md").parent.mkdir(exist_ok=True)
        (nm.notes_dir / ".templates" / "tmpl.md").write_text("template")
        folders = nm.get_folders()
        assert ".templates" not in folders

    def test_get_notes_in_folder(self, nm: NotesManager) -> None:
        nm.save_note("Work/A", "a")
        nm.save_note("Work/B", "b")
        nm.save_note("Other", "c")
        notes = nm.get_notes_in_folder("Work")
        assert "Work/A" in notes
        assert "Work/B" in notes
        assert "Other" not in notes

    def test_reserve_name_in_folder(self, nm: NotesManager) -> None:
        nm.save_note("Work/Untitled", "")
        assert nm.reserve_name("Work/Untitled") == "Work/Untitled 1"

    def test_encrypted_folder_round_trip(self, nm: NotesManager) -> None:
        nm.save_encrypted("Work/Month/Secret", b"\xde\xad")
        assert nm.is_encrypted("Work/Month/Secret")
        raw = nm.read_encrypted_raw("Work/Month/Secret")
        assert raw == b"\xde\xad"

    def test_stale_temp_recursive(self, tmp_path) -> None:
        (tmp_path / "Work" / ".Old.tmp").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "Work" / ".Old.tmp").write_text("stale", encoding="utf-8")
        (tmp_path / "Work" / "Secret.md.enc.new").write_bytes(b"stale")
        NotesManager(notes_dir=tmp_path)
        assert not (tmp_path / "Work" / ".Old.tmp").exists()
        assert not (tmp_path / "Work" / "Secret.md.enc.new").exists()


class TestGetNotesWithSearch:
    def test_search_by_name(self, nm: NotesManager) -> None:
        nm.save_note("Shopping List", "eggs")
        nm.save_note("Work Notes", "meeting")
        results = nm.get_notes(search_text="shop")
        assert "Shopping List" in results
        assert "Work Notes" not in results

    def test_search_by_content(self, nm: NotesManager) -> None:
        nm.save_note("Daily", "remember to buy milk")
        nm.save_note("Journal", "weather was nice")
        results = nm.get_notes(search_text="milk")
        assert "Daily" in results
        assert "Journal" not in results

    def test_search_case_insensitive(self, nm: NotesManager) -> None:
        nm.save_note("My Note", "Hello World")
        results = nm.get_notes(search_text="hello")
        assert "My Note" in results

    def test_search_no_match(self, nm: NotesManager) -> None:
        nm.save_note("Note", "content")
        assert nm.get_notes(search_text="zzzzz") == []


class TestGetEncryptedNotes:
    def test_empty_when_no_encrypted(self, nm: NotesManager) -> None:
        nm.save_note("Note", "content")
        assert nm.get_encrypted_notes() == set()

    def test_finds_encrypted(self, nm: NotesManager) -> None:
        nm.save_encrypted("Secret", b"\x00\x01")
        result = nm.get_encrypted_notes()
        assert "Secret" in result

    def test_ignores_plain_notes(self, nm: NotesManager) -> None:
        nm.save_note("Plain", "text")
        nm.save_encrypted("Hidden", b"\x00")
        result = nm.get_encrypted_notes()
        assert "Plain" not in result
        assert "Hidden" in result


class TestReadEncryptedRawErrors:
    def test_raises_on_missing(self, nm: NotesManager) -> None:
        with pytest.raises(FileNotFoundError):
            nm.read_encrypted_raw("Ghost")


class TestRenameEncrypted:
    def test_rename_encrypted_note(self, nm: NotesManager) -> None:
        nm.save_encrypted("Old Secret", b"\x00\x01\x02")
        assert nm.rename_note("Old Secret", "New Secret")
        assert "Old Secret" not in nm.get_notes()
        assert nm.read_encrypted_raw("New Secret") == b"\x00\x01\x02"

    def test_rename_preserves_content(self, nm: NotesManager) -> None:
        nm.save_note("Plain", "content")
        nm.save_encrypted("Enc", b"\xab\xcd")
        assert nm.rename_note("Enc", "Enc Renamed")
        assert nm.read_encrypted_raw("Enc Renamed") == b"\xab\xcd"
        assert nm.read_plain("Plain") == "content"


class TestGetMetadataNonExistent:
    def test_non_existent_returns_empty(self, nm: NotesManager) -> None:
        meta = nm.get_metadata("Does Not Exist")
        assert meta["snippet"] == ""
        assert meta["links"] == []
        assert meta["checkboxes"] == []
        assert meta["mtime"] == 0


class TestBoundedDict:
    def test_evicts_oldest(self) -> None:
        from core.storage import _BoundedDict

        d = _BoundedDict(maxlen=3)
        d["a"] = 1
        d["b"] = 2
        d["c"] = 3
        d["d"] = 4
        assert "a" not in d
        assert "b" in d
        assert d["d"] == 4

    def test_keeps_within_maxlen(self) -> None:
        from core.storage import _BoundedDict

        d = _BoundedDict(maxlen=5)
        for i in range(100):
            d[str(i)] = i
        assert len(d) == 5
        assert "0" not in d
        assert "99" in d


class TestGetBacklinks:
    def test_no_backlinks(self, nm: NotesManager) -> None:
        nm.save_note("Orphan", "just text")
        assert nm.get_backlinks("Orphan", set()) == []

    def test_finds_backlinks(self, nm: NotesManager) -> None:
        nm.save_note("Source", "Check [[Target]] for details")
        nm.save_note("Target", "some content")
        backlinks = nm.get_backlinks("Target", set())
        assert "Source" in backlinks

    def test_excludes_archived(self, nm: NotesManager) -> None:
        nm.save_note("Source", "See [[Target]]")
        nm.save_note("Target", "content")
        backlinks = nm.get_backlinks("Target", {"Source"})
        assert "Source" not in backlinks

    def test_self_link_excluded(self, nm: NotesManager) -> None:
        nm.save_note("Alone", "[[Alone]]")
        backlinks = nm.get_backlinks("Alone", set())
        assert "Alone" not in backlinks

    def test_backlinks_removed_when_source_changes(self, nm: NotesManager) -> None:
        nm.save_note("Source", "See [[Target]]")
        nm.save_note("Target", "content")
        assert "Source" in nm.get_backlinks("Target", set())

        nm.save_note("Source", "No link anymore")

        assert "Source" not in nm.get_backlinks("Target", set())

    def test_backlinks_survive_target_resave(self, nm: NotesManager) -> None:
        nm.save_note("Source", "See [[Target]]")
        nm.save_note("Target", "old content")
        nm.save_note("Target", "new content")

        assert "Source" in nm.get_backlinks("Target", set())

    def test_backlinks_update_when_source_renamed(self, nm: NotesManager) -> None:
        nm.save_note("Source", "See [[Target]]")
        nm.save_note("Target", "content")
        assert nm.rename_note("Source", "Renamed Source")

        backlinks = nm.get_backlinks("Target", set())
        assert "Renamed Source" in backlinks
        assert "Source" not in backlinks

    def test_backlinks_removed_when_source_deleted(self, nm: NotesManager) -> None:
        nm.save_note("Source", "See [[Target]]")
        nm.save_note("Target", "content")
        nm.delete_note("Source")

        assert "Source" not in nm.get_backlinks("Target", set())

    def test_backlinks_removed_when_source_encrypted(self, nm: NotesManager) -> None:
        nm.save_note("Source", "See [[Target]]")
        nm.save_note("Target", "content")
        assert "Source" in nm.get_backlinks("Target", set())

        nm.save_encrypted("Source", b"ciphertext")

        assert "Source" not in nm.get_backlinks("Target", set())
