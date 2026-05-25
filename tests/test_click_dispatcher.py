"""Tests for click-dispatcher URL safety helpers."""

from __future__ import annotations

import pytest

from ui.click_dispatcher import _is_safe_url


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com/path"])
def test_safe_url_allows_http_and_https(url: str) -> None:
    assert _is_safe_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "mailto:user@example.com",
        "data:text/html,<script>",
    ],
)
def test_safe_url_rejects_non_browser_schemes(url: str) -> None:
    assert not _is_safe_url(url)
