"""Microphone toggle button for speech-to-text dictation."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from gi.repository import GLib, Gtk


class SpeechButton(Gtk.ToggleButton):
    def __init__(
        self,
        assets_dir: Path,
        get_buffer: Callable,
        language: str | None = None,
        input_device: str | int | None = None,
        on_recording: Callable[[bool], None] | None = None,
        on_transcribing: Callable[[bool], None] | None = None,
        on_quiet_audio: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._get_buffer = get_buffer
        self._on_recording = on_recording
        self._on_transcribing = on_transcribing
        self._on_quiet_audio = on_quiet_audio
        self._language = language
        self._input_device = input_device
        self._client: object | None = None
        self.add_css_class("toolbar-btn")
        self.set_tooltip_text("Dictate (Ctrl+Space)")

        icon_path = assets_dir / "microphone.svg"
        if icon_path.exists():
            img = Gtk.Image.new_from_file(str(icon_path))
            img.set_pixel_size(16)
            self.set_child(img)
        else:
            self.set_label("Mic")

        self._toggled_id = self.connect("toggled", self._on_toggled)

    def _lazy_client(self):
        if self._client is not None:
            return self._client
        from core.speech_client import SpeechWorkerClient

        self._client = SpeechWorkerClient(
            language=self._language,
            input_device=self._input_device,
            on_final=lambda text: GLib.idle_add(self._insert_text, text),
            on_quiet_audio=lambda: (
                GLib.idle_add(self._on_quiet_audio) if self._on_quiet_audio else None
            ),
        )
        return self._client

    def _shutdown_client(self) -> None:
        if self._client is not None:
            self._client.shutdown()
            self._client = None

    def update_input_device(self, device: str | int | None) -> None:
        self._input_device = device
        self._shutdown_client()

    def update_language(self, language: str | None) -> None:
        self._language = language
        self._shutdown_client()

    def _start_recording(self) -> None:
        self.add_css_class("recording")
        self._lazy_client().start_recording()
        if self._on_recording:
            self._on_recording(True)

    def _on_toggled(self, btn: Gtk.ToggleButton) -> None:
        if btn.get_active():
            self._start_recording()
        else:
            self.remove_css_class("recording")
            if self._client is not None:
                self._client.stop_recording()
            if self._on_recording:
                self._on_recording(False)
            if self._on_transcribing:
                self._on_transcribing(True)

    def _insert_text(self, text: str) -> bool:
        buf = self._get_buffer()
        if buf is not None:
            buf.insert_at_cursor(text + " ")
        self.set_active(False)
        if self._on_transcribing:
            self._on_transcribing(False)
        return False
