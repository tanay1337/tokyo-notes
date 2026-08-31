"""Tests for the dedicated PDF reader helpers."""

from __future__ import annotations

from ui.pdf_reader import PdfReaderView


class _PdfConfig:
    def __init__(self) -> None:
        self.states: dict[str, dict] = {}

    def get_pdf_state(self, key: str) -> dict:
        return self.states.get(key, {})

    def set_pdf_state(self, key: str, state: dict) -> None:
        self.states[key] = state


class _Adjustment:
    def __init__(self, value: float, upper: float = 200.0, page_size: float = 100.0):
        self._value = value
        self._upper = upper
        self._page_size = page_size

    def get_upper(self) -> float:
        return self._upper

    def get_page_size(self) -> float:
        return self._page_size

    def get_value(self) -> float:
        return self._value


class _Allocation:
    def __init__(self, y: int, height: int):
        self.y = y
        self.height = height


class _PageWidget:
    def __init__(self, y: int, height: int):
        self._allocation = _Allocation(y, height)

    def get_allocation(self) -> _Allocation:
        return self._allocation


def _reader() -> PdfReaderView:
    reader = PdfReaderView.__new__(PdfReaderView)
    reader._note_name = "Research"
    reader._source_ref = "docs/paper.pdf"
    reader._config_manager = _PdfConfig()
    reader._page_count = 10
    reader._page = 2
    reader._zoom = 1.0
    reader._scroll_by_page = {2: 0.5}
    reader._pdf_path = None
    reader._vadj = _Adjustment(0.0)
    reader._page_widgets = []
    reader._pending_state_save = 0
    reader._suspend_scroll_save = False
    reader._current_title = "PDF Reader"
    reader._download_thread = None
    reader._loading_source = None
    reader._return_view = "editor"
    return reader


def test_state_key_combines_note_and_source() -> None:
    reader = _reader()

    assert reader._state_key() == "Research::docs/paper.pdf"


def test_clamp_helpers_bound_values() -> None:
    reader = _reader()

    assert reader._clamp_page(-1) == 0
    assert reader._clamp_page(999) == 9
    assert reader._clamp_zoom(0.1) == 0.5
    assert reader._clamp_zoom(9.0) == 3.0


def test_parse_scroll_state_filters_bad_entries() -> None:
    reader = _reader()

    parsed = reader._parse_scroll_state({"1": 0.7, "bad": "x", 3: 2.0})

    assert parsed == {1: 0.7, 3: 1.0}


def test_parse_scalar_state_helpers_use_fallbacks() -> None:
    assert PdfReaderView._parse_int("bad", 4) == 4
    assert PdfReaderView._parse_int("2", 0) == 2
    assert PdfReaderView._parse_float("bad", 1.25) == 1.25
    assert PdfReaderView._parse_float("1.5", 1.0) == 1.5


def test_save_state_writes_pdf_state_json_shape() -> None:
    reader = _reader()

    reader._save_state()

    assert reader._config_manager.states["Research::docs/paper.pdf"] == {
        "page": 2,
        "zoom": 1.0,
        "scroll_by_page": {"2": 0.5},
    }


def test_page_count_delegates_to_editor_pdf_backend() -> None:
    reader = _reader()

    class _Editor:
        def _get_pdf_page_count(self, path):
            return 12

    class _App:
        editor = _Editor()

    reader.app = _App()

    assert reader._get_pdf_page_count("paper.pdf") == 12


def test_flush_state_captures_current_scroll_ratio() -> None:
    reader = _reader()
    reader._pdf_path = object()
    reader._vadj = _Adjustment(125.0, upper=400.0, page_size=75.0)
    reader._page_widgets = [
        _PageWidget(0, 100),
        _PageWidget(100, 100),
        _PageWidget(200, 100),
    ]

    reader._flush_state()

    assert reader._config_manager.states["Research::docs/paper.pdf"][
        "scroll_by_page"
    ] == {"1": 0.25, "2": 0.5}


def test_nearest_page_for_viewport_uses_viewport_center() -> None:
    page = PdfReaderView._nearest_page_for_viewport(
        [(0.0, 100.0), (120.0, 100.0), (240.0, 100.0)],
        scroll_value=100.0,
        page_size=80.0,
    )

    assert page == 1
