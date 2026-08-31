"""Tests for core/config.py — ConfigManager persistence."""

from __future__ import annotations

from pathlib import Path

from core.config import ConfigManager


def _cfg(tmp_path):
    """Return a ConfigManager isolated to *tmp_path*."""

    orig = getattr(Path, "home", None)
    try:
        # Override Path.home() so the config dir lands in tmp_path
        Path.home = lambda: tmp_path
        return ConfigManager()
    finally:
        if orig:
            Path.home = orig


def _cfg_clean(tmp_path):
    """Same as _cfg but also ensures no stale files exist from prior runs."""
    return _cfg(tmp_path)


class TestConfigGet:
    def test_get_default(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.get("theme") == "tokyo-night"

    def test_get_custom_fallback(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.get("nonexistent", "default") == "default"

    def test_get_nonexistent_returns_none(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.get("nonexistent") is None

    def test_get_after_set(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.data["theme"] = "nord"
        assert cfg.get("theme") == "nord"


class TestConfigSet:
    def test_set_new_value(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set("theme", "nord")
        assert cfg.get("theme") == "nord"

    def test_set_same_value_is_noop(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set("theme", "tokyo-night")  # already the default
        assert not cfg._dirty

    def test_set_triggers_dirty(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set("theme", "nord")
        assert cfg._dirty is True

    def test_set_schedules_flush(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set("theme", "nord")
        # GLib.timeout_add is mocked; calling it sets _flush_timer to non-zero
        assert cfg._flush_timer != 0

    def test_set_resets_existing_timer(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg._flush_timer = 123  # simulate existing timer
        cfg.set("theme", "nord")
        # Old timer should have been cancelled (GLib.source_remove called)
        assert cfg._flush_timer != 123


class TestConfigFlush:
    def test_flush_writes_to_disk(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set("theme", "nord")
        cfg.flush_immediate()
        saved = cfg.config_path.read_text(encoding="utf-8")
        assert "nord" in saved

    def test_flush_clears_dirty(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set("theme", "nord")
        cfg.flush_immediate()
        assert not cfg._dirty

    def test_flush_idempotent(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.flush_immediate()  # nothing dirty — should not crash
        assert not cfg._dirty


class TestConfigPdfState:
    def test_pdf_state_persists_to_separate_file(self, tmp_path):
        cfg = _cfg(tmp_path)

        cfg.set_pdf_state("Note::doc.pdf", {"page": 3, "total_pages": 8})

        assert cfg.pdf_state_path.name == "pdf_state.json"
        assert cfg.get_pdf_state("Note::doc.pdf") == {"page": 3, "total_pages": 8}
        assert "Note::doc.pdf" in cfg.pdf_state_path.read_text(encoding="utf-8")

        cfg2 = _cfg(tmp_path)
        assert cfg2.get_pdf_state("Note::doc.pdf") == {"page": 3, "total_pages": 8}

    def test_pdf_state_missing_key_returns_empty_dict(self, tmp_path):
        cfg = _cfg(tmp_path)

        assert cfg.get_pdf_state("missing") == {}

    def test_rename_note_migrates_pdf_state_keys(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.set_pdf_state("Old Note::docs/paper.pdf", {"page": 4})
        cfg.set_pdf_state("Other Note::docs/paper.pdf", {"page": 1})

        cfg.rename_note_in_config("Old Note", "New Note")

        assert cfg.get_pdf_state("Old Note::docs/paper.pdf") == {}
        assert cfg.get_pdf_state("New Note::docs/paper.pdf") == {"page": 4}
        assert cfg.get_pdf_state("Other Note::docs/paper.pdf") == {"page": 1}

        cfg2 = _cfg(tmp_path)
        assert cfg2.get_pdf_state("Old Note::docs/paper.pdf") == {}
        assert cfg2.get_pdf_state("New Note::docs/paper.pdf") == {"page": 4}


class TestConfigPinUnpin:
    def test_pin(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.pin("Note A")
        assert "Note A" in cfg.pinned

    def test_unpin(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.pin("Note A")
        cfg.unpin("Note A")
        assert "Note A" not in cfg.pinned

    def test_pin_idempotent(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.pin("Note A")
        cfg.pin("Note A")  # should not raise or duplicate
        assert len(cfg.pinned) == 1

    def test_unpin_nonexistent(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.unpin("Ghost")  # should not raise

    def test_pin_persists(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.pin("Persistent")
        saved = cfg.pinned_path.read_text(encoding="utf-8")
        assert "Persistent" in saved


class TestConfigArchive:
    def test_toggle_archive(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert not cfg.is_archived("N")
        cfg.toggle_archive("N")
        assert cfg.is_archived("N")
        cfg.toggle_archive("N")
        assert not cfg.is_archived("N")

    def test_is_archived_false_for_unknown(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert not cfg.is_archived("Unknown")


class TestConfigEncrypted:
    def test_mark_encrypted(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.mark_encrypted("Private")
        assert "Private" in cfg.encrypted

    def test_mark_decrypted(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.mark_encrypted("Private")
        cfg.mark_decrypted("Private")
        assert "Private" not in cfg.encrypted

    def test_sync_encrypted_set(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.mark_encrypted("A")
        cfg.sync_encrypted_set({"B", "C"})
        assert cfg.encrypted == {"B", "C"}

    def test_sync_encrypted_set_noop(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.mark_encrypted("A")
        cfg.sync_encrypted_set({"A"})
        assert cfg.encrypted == {"A"}


class TestConfigRemoveNote:
    def test_remove_cleanup_all_sets(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.pin("N")
        cfg.toggle_archive("N")
        cfg.mark_encrypted("N")
        cfg.remove_note("N")
        assert "N" not in cfg.pinned
        assert "N" not in cfg.archived
        assert "N" not in cfg.encrypted

    def test_remove_nonexistent(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.remove_note("Ghost")  # should not raise


class TestConfigLoadsExisting:
    def test_loads_saved_pins(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.pin("Survive")
        cfg2 = _cfg(tmp_path)
        assert "Survive" in cfg2.pinned

    def test_loads_saved_archived(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.toggle_archive("Old")
        cfg2 = _cfg(tmp_path)
        assert cfg2.is_archived("Old")

    def test_loads_saved_encrypted(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.mark_encrypted("Secret")
        cfg2 = _cfg(tmp_path)
        assert "Secret" in cfg2.encrypted


class TestConfigInvalidJson:
    def test_corrupt_config_json_uses_defaults(self, tmp_path):
        config_dir = tmp_path / ".config" / "tokyo-notes"
        config_dir.mkdir(parents=True)
        (config_dir / "tokyo-notes.json").write_text("{not-json", encoding="utf-8")

        cfg = _cfg(tmp_path)

        assert cfg.get("theme") == "tokyo-night"

    def test_wrong_type_json_uses_defaults(self, tmp_path):
        config_dir = tmp_path / ".config" / "tokyo-notes"
        config_dir.mkdir(parents=True)
        (config_dir / "pinned.json").write_text('{"not": "a list"}', encoding="utf-8")

        cfg = _cfg(tmp_path)

        assert cfg.pinned == set()
