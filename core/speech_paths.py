"""Cross-platform helpers for locating/checking the dictation backend.

Linux:   lazily-provisioned venv under ~/.local/share/tokyo-notes/.
macOS:   dictation deps are either bundled at build time (dictation variant)
         or absent entirely (standard variant).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from gi.repository import GLib

_BASE = Path(GLib.get_user_data_dir()) / "tokyo-notes"
SPEECH_VENV = _BASE / "speech-venv"
SPEECH_PYTHON = SPEECH_VENV / "bin" / "python3"
SPEECH_PIP = SPEECH_VENV / "bin" / "pip"
SPEECH_PYVENV_CFG = SPEECH_VENV / "pyvenv.cfg"

REQUIRED_PACKAGES = [
    "faster-whisper>=1.0.0",
    "sounddevice>=0.4.6",
    "huggingface_hub",
    "numpy",
]


def _speech_site_packages() -> Path | None:
    """Return the speech venv site-packages path, or None if it doesn't exist."""
    site_packages = (
        SPEECH_VENV
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    return site_packages if site_packages.is_dir() else None


def dictation_bundled() -> bool:
    """True if faster-whisper is available in-process (macOS dictation build)."""
    return (
        importlib.util.find_spec("faster_whisper") is not None
        and importlib.util.find_spec("sounddevice") is not None
    )


def import_sounddevice():
    """Import sounddevice, falling back to the speech venv site-packages."""
    try:
        import sounddevice as sd
    except ModuleNotFoundError:
        site_packages = (
            SPEECH_VENV
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        if site_packages.is_dir():
            sys.path.insert(0, str(site_packages))
        import sounddevice as sd
    return sd


def import_numpy():
    """Import numpy, falling back to the speech venv site-packages."""
    try:
        import numpy as np
    except ModuleNotFoundError:
        site_packages = (
            SPEECH_VENV
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        if site_packages.is_dir():
            sys.path.insert(0, str(site_packages))
        import numpy as np
    return np


def import_faster_whisper():
    """Import faster_whisper, falling back to the speech venv site-packages.

    Returns the faster_whisper module, or None if it's not available.
    """
    try:
        import faster_whisper as fw
    except ModuleNotFoundError:
        site_packages = _speech_site_packages()
        if site_packages is not None:
            sys.path.insert(0, str(site_packages))
        try:
            import faster_whisper as fw
        except ModuleNotFoundError:
            return None
    return fw


def import_huggingface_hub():
    """Import huggingface_hub, falling back to the speech venv site-packages."""
    try:
        import huggingface_hub as hf
    except ModuleNotFoundError:
        site_packages = (
            SPEECH_VENV
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        if site_packages.is_dir():
            sys.path.insert(0, str(site_packages))
        import huggingface_hub as hf
    return hf


def is_available_for_build() -> bool:
    """True if this build/OS can support dictation at all.

    On Linux the venv can always be provisioned on demand.
    On macOS availability is fixed at build time (standard vs dictation variant).
    """
    if sys.platform == "darwin":
        return dictation_bundled()
    return True


def is_available() -> bool:
    """True if dictation deps are usable right now (no provisioning needed)."""
    if sys.platform == "darwin":
        return dictation_bundled()
    return SPEECH_PYTHON.is_file() and SPEECH_PYVENV_CFG.is_file()
