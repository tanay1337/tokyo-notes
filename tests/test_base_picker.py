"""Regression tests for picker keyboard navigation and teardown."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.base_picker import SearchablePicker


def test_up_without_selection_chooses_last_row() -> None:
    last_row = MagicMock()
    list_box = MagicMock()
    list_box.get_selected_row.return_value = None
    list_box.get_last_child.return_value = last_row
    picker = SimpleNamespace(list_box=list_box)

    SearchablePicker._select_relative(picker, -1)

    list_box.select_row.assert_called_once_with(last_row)


def test_down_without_selection_chooses_first_row() -> None:
    first_row = MagicMock()
    list_box = MagicMock()
    list_box.get_selected_row.return_value = None
    list_box.get_row_at_index.return_value = first_row
    picker = SimpleNamespace(list_box=list_box)

    SearchablePicker._select_relative(picker, 1)

    list_box.get_row_at_index.assert_called_once_with(0)
    list_box.select_row.assert_called_once_with(first_row)


def test_deferred_unparent_is_safe_after_parent_already_removed() -> None:
    picker = SimpleNamespace(
        get_parent=MagicMock(return_value=None), unparent=MagicMock()
    )

    assert SearchablePicker._deferred_unparent(picker) is False

    picker.unparent.assert_not_called()
