"""Tests for assistant providers without network access."""

from __future__ import annotations

import io
import json

import pytest

from core.ai import (
    CancelToken,
    ChatMessage,
    ChatRequest,
    GenerationCancelled,
    LlamaCppProvider,
    is_loopback_url,
    with_port,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_loopback_url_validation() -> None:
    assert is_loopback_url("http://127.0.0.1:8080/v1")
    assert is_loopback_url("http://localhost:9000")
    assert not is_loopback_url("https://example.com/v1")


def test_llama_adds_v1_to_bare_server_url() -> None:
    assert LlamaCppProvider("http://localhost:8080").base_url.endswith("/v1")


def test_with_port_replaces_server_port() -> None:
    assert with_port("http://127.0.0.1:9000/v1", 8080) == "http://127.0.0.1:8080/v1"


def test_llama_stream_and_payload(monkeypatch) -> None:
    seen = {}
    events = (
        b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"!"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def fake_open(req, timeout):
        seen["url"] = req.full_url
        seen["payload"] = json.loads(req.data)
        return _Response(events)

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    provider = LlamaCppProvider()
    request = ChatRequest("local", (ChatMessage("user", "Hello"),), "Safe")
    assert "".join(provider.stream_chat(request, CancelToken())) == "Hi!"
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["payload"]["messages"][0]["role"] == "system"


def test_llama_discovers_loaded_models(monkeypatch) -> None:
    payload = b'{"data":[{"id":"qwen-local"},{"id":"llama-local"}]}'
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _Response(payload))

    assert LlamaCppProvider().list_models() == ["llama-local", "qwen-local"]


def test_llama_api_key_is_sent_as_bearer(monkeypatch) -> None:
    seen = {}

    def fake_open(req, timeout):
        seen["authorization"] = req.get_header("Authorization")
        return _Response(b'{"data":[]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    LlamaCppProvider(api_key="local-secret").list_models()
    assert seen["authorization"] == "Bearer local-secret"


def test_cancelled_stream(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: _Response(b'data: {"choices":[]}\n\n'),
    )
    token = CancelToken()
    token.cancel()
    provider = LlamaCppProvider()
    with pytest.raises(GenerationCancelled):
        list(
            provider.stream_chat(
                ChatRequest("m", (ChatMessage("user", "x"),), "i"), token
            )
        )
