"""Regression tests for the world-time widget's periodic refresh lifecycle."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.widgets.worldtime import _UPDATE_MS, WorldTimeWidget


def test_hidden_widget_does_not_start_periodic_refresh() -> None:
    widget = SimpleNamespace(
        _timer_id=None,
        get_mapped=MagicMock(return_value=False),
        stop_periodic=MagicMock(),
        _update_times=MagicMock(),
    )

    with patch("ui.widgets.worldtime.GLib.timeout_add") as timeout_add:
        WorldTimeWidget._start_timer(widget)

    widget.stop_periodic.assert_called_once_with()
    timeout_add.assert_not_called()


def test_mapped_widget_starts_periodic_refresh() -> None:
    update_times = MagicMock()
    widget = SimpleNamespace(
        _timer_id=None,
        get_mapped=MagicMock(return_value=True),
        stop_periodic=MagicMock(),
        _update_times=update_times,
    )

    with patch("ui.widgets.worldtime.GLib.timeout_add", return_value=42) as timeout_add:
        WorldTimeWidget._start_timer(widget)

    timeout_add.assert_called_once_with(_UPDATE_MS, update_times)
    assert widget._timer_id == 42
