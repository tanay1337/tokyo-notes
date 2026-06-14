"""Utility functions for offline speech-to-text dictation."""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

_MODEL_SIZE = "base"
# faster-whisper repo IDs (hardcoded — avoids importing faster_whisper in main process)
_WHISPER_REPOS: dict[str, str] = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def _repo_id(size: str) -> str:
    return _WHISPER_REPOS.get(size, _WHISPER_REPOS[_MODEL_SIZE])


def model_cached(size: str = _MODEL_SIZE) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        repo_id = _repo_id(size)
        path = try_to_load_from_cache(repo_id, "model.bin")
        return path is not None
    except Exception:
        return False


def list_input_devices() -> list[dict]:
    """Return input devices, skipping ALSA virtual devices and non-mic nodes."""
    from core.speech_paths import import_sounddevice

    sd = import_sounddevice()

    _VIRTUAL_NAMES = frozenset(
        {
            "sysdefault",
            "lavrate",
            "samplerate",
            "speexrate",
            "pipewire",
            "pulse",
            "speex",
            "upmix",
            "vdownmix",
            "default",
            "dmix",
            "jack",
        }
    )
    _OUTPUT_KEYWORDS = frozenset(
        {
            "hdmi",
            "displayport",
            "speaker",
            "firefox",
            "chrome",
            "loopback",
            "monitor",
            "output",
        }
    )
    devices = []
    seen: set[str] = set()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] == 0:
            continue
        name = d["name"].strip().lower()
        stem = name.split(",")[0].strip()
        if stem in _VIRTUAL_NAMES:
            continue
        if any(s in name for s in _OUTPUT_KEYWORDS):
            continue
        if stem in seen:
            continue
        seen.add(stem)
        devices.append(
            {
                "index": i,
                "name": d["name"],
                "channels": d["max_input_channels"],
                "samplerate": d["default_samplerate"],
            }
        )
    return devices


def download_model_with_progress(
    size: str = _MODEL_SIZE,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    from huggingface_hub import snapshot_download
    from tqdm import tqdm

    repo_id = _repo_id(size)
    allow_patterns = [
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.*",
    ]

    class _ProgressTqdm(tqdm):
        def update(self, n=1):
            super().update(n)
            if self.total and on_progress:
                on_progress(self.n, self.total)

    return snapshot_download(
        repo_id,
        allow_patterns=allow_patterns,
        tqdm_class=_ProgressTqdm,
    )
