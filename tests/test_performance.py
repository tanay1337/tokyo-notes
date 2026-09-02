"""Tests for local slow-operation diagnostics."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import core.performance as performance


def test_slow_callback_logs_duration_and_line_count(monkeypatch, caplog) -> None:
    monkeypatch.setattr(performance, "_ENABLED", True)
    times = iter((10.0, 10.025))
    monkeypatch.setattr(performance, "perf_counter", lambda: next(times))

    class Worker:
        buffer = MagicMock()
        buffer.get_line_count.return_value = 17

        @performance.slow_callback("sample-operation")
        def run(self) -> str:
            return "done"

    with caplog.at_level(logging.WARNING, logger="performance"):
        assert Worker().run() == "done"

    assert "operation=sample-operation" in caplog.text
    assert "duration_ms=25.0" in caplog.text
    assert "thread=MainThread" in caplog.text
    assert "lines=17" in caplog.text


def test_fast_callback_does_not_log(monkeypatch, caplog) -> None:
    monkeypatch.setattr(performance, "_ENABLED", True)
    times = iter((2.0, 2.005))
    monkeypatch.setattr(performance, "perf_counter", lambda: next(times))

    @performance.slow_callback("fast")
    def run() -> None:
        return None

    with caplog.at_level(logging.WARNING, logger="performance"):
        run()

    assert "operation=fast" not in caplog.text


def test_diagnostics_are_disabled_by_default(monkeypatch, caplog) -> None:
    monkeypatch.setattr(performance, "_ENABLED", False)

    @performance.slow_callback("disabled")
    def run() -> str:
        return "done"

    with caplog.at_level(logging.WARNING, logger="performance"):
        assert run() == "done"

    assert "operation=disabled" not in caplog.text
