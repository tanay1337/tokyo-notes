"""Focused tests for settings behavior that does not require a GTK display."""

from __future__ import annotations

from types import SimpleNamespace

import ui.settings as settings_module
from ui.settings import SettingsView


class _Entry:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class _Status:
    def __init__(self) -> None:
        self.label = ""

    def set_label(self, label: str) -> None:
        self.label = label


class _Button:
    def __init__(self) -> None:
        self.sensitive = True

    def set_sensitive(self, sensitive: bool) -> None:
        self.sensitive = sensitive


def test_llama_connection_uses_server_url_as_single_source(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class Provider:
        def __init__(self, url: str, api_key: str = "") -> None:
            seen["url"] = url
            seen["api_key"] = api_key

        def list_models(self) -> list[str]:
            return ["local-model"]

    class ImmediateThread:
        def __init__(self, target, **_kwargs) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr(settings_module, "LlamaCppProvider", Provider)
    monkeypatch.setattr(settings_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        settings_module.GLib,
        "idle_add",
        lambda callback, *args: callback(*args),
    )

    status = _Status()
    button = _Button()
    view = SimpleNamespace(
        _llama_url_entry=_Entry("http://127.0.0.1:9090/v1"),
        _llama_api_key_entry=_Entry("secret"),
        _llama_test_status=status,
        _finish_llama_test=lambda test_button, models, error: (
            test_button.set_sensitive(True),
            status.set_label(error or f"Connected · {len(models)} model(s)"),
            False,
        )[-1],
    )

    SettingsView._test_llama_connection(view, button)

    assert seen == {
        "url": "http://127.0.0.1:9090/v1",
        "api_key": "secret",
    }
    assert button.sensitive
    assert status.label == "Connected · 1 model(s)"
