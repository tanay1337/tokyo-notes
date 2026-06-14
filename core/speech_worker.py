"""Transcription backend for offline speech-to-text.

Two entry points:
  transcribe(audio, language)  — importable function for in-process use.
  main()                       — stdin/stdout JSON loop for subprocess use.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from io import BytesIO
from typing import Callable

import numpy as np
from faster_whisper import WhisperModel

_WHISPER_SR = 16_000
_MODEL_SIZE = "base"

logger = logging.getLogger("speech_worker")

_model: WhisperModel | None = None


def _ensure_model() -> None:
    global _model
    if _model is None:
        _model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")


def transcribe(
    audio: np.ndarray,
    language: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    _ensure_model()
    segments, _ = _model.transcribe(
        audio,
        language=language or None,
        vad_filter=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def _decode_audio(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    buf = BytesIO(raw)
    return np.frombuffer(buf.read(), dtype=np.float32)


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    _ensure_model()

    def respond(obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            respond({"error": f"invalid json: {e}"})
            continue

        if req.get("exit"):
            break

        b64 = req.get("audio")
        if not b64:
            respond({"error": "missing audio field"})
            continue

        try:
            audio = _decode_audio(b64)
        except Exception as e:
            respond({"error": f"decode failed: {e}"})
            continue

        duration = len(audio) / _WHISPER_SR
        if duration < 0.3:
            respond({"text": ""})
            continue

        rms = np.sqrt(np.mean(audio**2))
        if rms < 0.0003:
            respond({"text": ""})
            continue

        language = req.get("language")
        try:
            text = transcribe(audio, language=language)
            respond({"text": text})
        except Exception as e:
            logger.exception("transcribe failed")
            respond({"error": str(e)})


if __name__ == "__main__":
    main()
