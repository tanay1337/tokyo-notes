"""SpeechWorkerClient — manages transcription backend and captures audio.

Two backends:
  * subprocess (Linux venv) — sends base64 audio via stdin/stdout JSON.
  * inline    (macOS dictation build) — calls speech_worker.transcribe()
                                         on a background thread.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import queue
import subprocess
import threading
from io import BytesIO
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import sounddevice as sd

import numpy as np

from core.speech_paths import SPEECH_PYTHON, import_sounddevice

logger = logging.getLogger(__name__)

_WHISPER_SR = 16_000


class SpeechWorkerClient:
    def __init__(
        self,
        language: str | None = None,
        input_device: str | int | None = None,
        on_final: Callable[[str], None] | None = None,
        on_quiet_audio: Callable[[], None] | None = None,
    ) -> None:
        self._language = language
        self._input_device = input_device
        self.on_final = on_final
        self._on_quiet_audio = on_quiet_audio

        # Detect whether faster-whisper is available in-process.
        self._inline = importlib.util.find_spec("faster_whisper") is not None

        self._proc: subprocess.Popen | None = None
        self._audio_q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._transcribing = False
        self._capture_sr: int = _WHISPER_SR
        self._reader_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Backend lifecycle
    # ------------------------------------------------------------------

    def ensure_started(self) -> None:
        if self._inline:
            return
        if self._proc is not None:
            return
        logger.info("Starting speech worker subprocess")
        self._proc = subprocess.Popen(
            [str(SPEECH_PYTHON), "-m", "core.speech_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._reader_thread = threading.Thread(target=self._read_worker, daemon=True)
        self._reader_thread.start()

    def shutdown(self) -> None:
        if self._inline:
            return
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.stdin.write(json.dumps({"exit": True}) + "\n")
                proc.stdin.flush()
            except Exception:
                pass
            proc.wait(timeout=5)
            for f in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    f.close()
                except Exception:
                    pass

    def _read_worker(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in resp:
                logger.error("Worker error: %s", resp["error"])
            text = resp.get("text", "")
            if self.on_final:
                self.on_final(text)

    # ------------------------------------------------------------------
    # Audio capture
    # ------------------------------------------------------------------

    def start_recording(self) -> None:

        sd = import_sounddevice()

        if self._recording or self._transcribing:
            return
        self._recording = True
        self.ensure_started()
        with self._lock:
            self._audio_q = queue.Queue()
        try:
            dev, sr = self._pick_input_device(self._input_device)
            self._capture_sr = sr
            blocksize = sr // 10
            self._stream = sd.InputStream(
                device=dev,
                samplerate=sr,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            logger.error("Failed to open audio stream: %s", e)
            self._recording = False
            return
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.error("Error closing audio stream: %s", e)
            self._stream = None
        with self._lock:
            self._audio_q.put(None)

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)
        with self._lock:
            if self._audio_q is not None:
                self._audio_q.put(indata.copy().flatten())

    def _transcribe_worker(self) -> None:
        self._transcribing = True
        try:
            chunks: list = []
            while True:
                with self._lock:
                    q = self._audio_q
                chunk = q.get()
                if chunk is None:
                    break
                chunks.append(chunk)
            if not chunks:
                logger.warning("No audio captured")
                return
            audio_raw = np.concatenate(chunks)
            if self._capture_sr != _WHISPER_SR:
                duration = len(audio_raw) / self._capture_sr
                target_len = int(duration * _WHISPER_SR)
                audio = np.interp(
                    np.linspace(0, len(audio_raw) - 1, target_len),
                    np.arange(len(audio_raw)),
                    audio_raw,
                ).astype(np.float32)
            else:
                audio = audio_raw
            duration = len(audio) / _WHISPER_SR
            rms = np.sqrt(np.mean(audio**2))
            if rms < 0.0003:
                logger.warning("Audio too quiet (RMS=%.6f)", rms)
                if self._on_quiet_audio:
                    self._on_quiet_audio()
                if self.on_final:
                    self.on_final("")
                return
            if duration < 0.3:
                logger.warning("Audio too short (%0.1f s)", duration)
                if self.on_final:
                    self.on_final("")
                return
            if self._inline:
                self._transcribe_locally(audio)
            else:
                self._send_audio(audio)
        finally:
            self._transcribing = False

    def _transcribe_locally(self, audio: np.ndarray) -> None:
        try:
            from core.speech_worker import transcribe

            text = transcribe(audio, language=self._language)
            if self.on_final:
                self.on_final(text)
        except Exception:
            logger.exception("Local transcription failed")

    def _send_audio(self, audio: np.ndarray) -> None:
        buf = BytesIO()
        buf.write(audio.tobytes())
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        req = json.dumps({"audio": b64, "language": self._language})
        with self._lock:
            proc = self._proc
        if proc is not None and proc.stdin:
            try:
                proc.stdin.write(req + "\n")
                proc.stdin.flush()
            except Exception as e:
                logger.error("Failed to send audio to worker: %s", e)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_input_device(
        device_override: str | int | None = None,
    ) -> tuple[str, int]:

        sd = import_sounddevice()

        if device_override is not None:
            info = sd.query_devices(device_override, kind="input")
            sr = int(info["default_samplerate"]) or _WHISPER_SR
            return device_override, sr
        for candidate in ("pipewire", "pulse", "default"):
            try:
                info = sd.query_devices(candidate, kind="input")
                sr = int(info["default_samplerate"])
                sd.check_input_settings(
                    device=candidate, samplerate=sr, channels=1, dtype="float32"
                )
                return candidate, sr
            except Exception:
                continue
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                sr = int(d["default_samplerate"]) or _WHISPER_SR
                return i, sr
        return "default", _WHISPER_SR
