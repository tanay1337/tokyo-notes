"""Provider-neutral text generation clients for the assistant.

The clients deliberately expose a tiny surface: request construction, model
discovery, and a stream of text deltas.  They never know about notes or GTK.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator, Protocol


class AIProviderError(RuntimeError):
    """A user-presentable provider failure with no prompt data in its text."""


class GenerationCancelled(AIProviderError):
    pass


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    instructions: str
    max_output_tokens: int = 2048
    temperature: float = 0.1


@dataclass
class CancelToken:
    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class AIProvider(Protocol):
    name: str

    def list_models(self) -> list[str]: ...

    def stream_chat(
        self, request: ChatRequest, cancel: CancelToken
    ) -> Iterator[str]: ...


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("Server URL is required")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Server URL must be an http(s) URL")
    return value


def is_loopback_url(value: str) -> bool:
    """Return whether *value* targets an unambiguous loopback hostname."""
    try:
        host = urllib.parse.urlparse(normalize_base_url(value)).hostname
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def with_port(value: str, port: int) -> str:
    """Return *value* with an explicit TCP port, preserving its /v1 path."""
    normalized = normalize_base_url(value)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.hostname or ""
    host_part = f"[{host}]" if ":" in host else host
    return parsed._replace(netloc=f"{host_part}:{port}").geturl()


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        error = payload.get("error", {})
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    except Exception:
        pass
    return f"HTTP {exc.code}"


class _HTTPProvider:
    name = "provider"

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 60) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _open(self, path: str, payload: dict | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers(),
            method="GET" if payload is None else "POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise AIProviderError(_read_error(exc)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise AIProviderError("Could not connect to the model provider") from exc

    def list_models(self) -> list[str]:
        with self._open("/models") as response:
            try:
                payload = json.load(response)
                return sorted(
                    str(item["id"])
                    for item in payload.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                )
            except (ValueError, TypeError, KeyError) as exc:
                raise AIProviderError(
                    "Provider returned an invalid model list"
                ) from exc

    def _events(self, response, cancel: CancelToken) -> Iterator[dict]:
        """Parse data-only Server-Sent Events without retaining response bodies."""
        data_lines: list[str] = []
        for raw in response:
            if cancel.cancelled:
                response.close()
                raise GenerationCancelled("Generation stopped")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    text = "\n".join(data_lines)
                    data_lines.clear()
                    if text == "[DONE]":
                        return
                    try:
                        yield json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise AIProviderError(
                            "Provider returned an invalid stream"
                        ) from exc
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            try:
                yield json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                raise AIProviderError("Provider returned an invalid stream") from exc


class LlamaCppProvider(_HTTPProvider):
    name = "llama_cpp"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        api_key: str = "",
        port: int | None = None,
    ) -> None:
        if port is not None:
            base_url = with_port(base_url, port)
        parsed = urllib.parse.urlparse(normalize_base_url(base_url))
        if parsed.path in ("", "/"):
            base_url = normalize_base_url(base_url) + "/v1"
        super().__init__(base_url, api_key=api_key)

    def stream_chat(self, request: ChatRequest, cancel: CancelToken) -> Iterator[str]:
        messages = [{"role": "system", "content": request.instructions}]
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in request.messages
        )
        payload = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        with self._open("/chat/completions", payload) as response:
            for event in self._events(response, cancel):
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {}).get("content")
                    if isinstance(delta, str):
                        yield delta
