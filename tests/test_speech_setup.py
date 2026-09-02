"""Tests for idempotent Dictation environment provisioning."""

from __future__ import annotations

from pathlib import Path

import core.speech_paths as speech_paths
import core.speech_setup as speech_setup


def _patch_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    venv = tmp_path / "speech-venv"
    python = venv / "bin" / "python3"
    config = venv / "pyvenv.cfg"
    stamp = venv / ".tokyo-notes-requirements"
    monkeypatch.setattr(speech_paths, "SPEECH_VENV", venv)
    monkeypatch.setattr(speech_paths, "SPEECH_PYTHON", python)
    monkeypatch.setattr(speech_paths, "SPEECH_PYVENV_CFG", config)
    monkeypatch.setattr(speech_paths, "SPEECH_REQUIREMENTS_STAMP", stamp)
    return python, config, stamp


def test_venv_valid_requires_matching_fingerprint(monkeypatch, tmp_path) -> None:
    python, config, stamp = _patch_paths(monkeypatch, tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    config.write_text("home = /usr/bin\n", encoding="utf-8")

    assert not speech_setup.venv_valid()

    stamp.write_text(speech_setup.requirements_fingerprint() + "\n", encoding="utf-8")
    assert speech_setup.venv_valid()

    stamp.write_text("outdated\n", encoding="utf-8")
    assert not speech_setup.venv_valid()


def test_provision_records_fingerprint_only_after_success(
    monkeypatch, tmp_path
) -> None:
    python, config, stamp = _patch_paths(monkeypatch, tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    config.write_text("home = /usr/bin\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        speech_setup,
        "_run",
        lambda cmd, _stdout, _stderr: calls.append(cmd),
    )

    speech_setup.provision()

    assert len(calls) == 1
    assert calls[0][1:4] == ["-m", "pip", "install"]
    assert stamp.read_text(encoding="utf-8").strip() == (
        speech_setup.requirements_fingerprint()
    )
    assert speech_setup.venv_valid()
