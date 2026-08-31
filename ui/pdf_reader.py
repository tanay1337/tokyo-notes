"""Dedicated in-app PDF reader view."""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from core.translations import tr
from core.utils import urlopen_with_fallback
from ui.editor import resolve_image_path

if TYPE_CHECKING:
    from main import TokyoNotes


def _icon_button(icon_name: str, tooltip: str, on_click) -> Gtk.Button:
    btn = Gtk.Button()
    img = Gtk.Image.new_from_icon_name(icon_name)
    img.set_pixel_size(16)
    btn.set_child(img)
    btn.add_css_class("flat")
    btn.add_css_class("header-btn")
    btn.set_tooltip_text(tooltip)
    btn.connect("clicked", on_click)
    return btn


class PdfReaderView(Gtk.Box):
    """A dedicated reader surface for PDFs embedded in notes."""

    def __init__(self, app: TokyoNotes) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app
        self._config_manager = app.cfg
        self._note_name: str = ""
        self._source_ref: str = ""
        self._return_view: str = "editor"
        self._pdf_path: Path | None = None
        self._page_count: int = 0
        self._page: int = 0
        self._zoom: float = 1.0
        self._page_widgets: list[Gtk.Widget] = []
        self._scroll_by_page: dict[int, float] = {}
        self._pending_state_save: int = 0
        self._suspend_scroll_save: bool = False
        self._current_title: str = tr("PDF Reader")
        self._download_thread: threading.Thread | None = None
        self._loading_source: str | None = None
        self._message_action_callback: Any | None = None
        self._updating_controls: bool = False
        self._render_generation: int = 0

        self._build_ui()

    def _build_ui(self) -> None:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(8)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)

        back_btn = _icon_button(
            "go-previous-symbolic", tr("Back"), lambda *_: self._go_back()
        )
        toolbar.append(back_btn)

        self._title_label = Gtk.Label(xalign=0)
        self._title_label.set_hexpand(True)
        self._title_label.add_css_class("view-title")
        toolbar.append(self._title_label)

        page_prev = _icon_button(
            "go-previous-symbolic", tr("Previous page"), lambda *_: self._go_page(-1)
        )
        toolbar.append(page_prev)

        self._page_spin = Gtk.SpinButton()
        self._page_spin.set_numeric(True)
        self._page_spin.set_width_chars(4)
        self._page_spin.connect("value-changed", self._on_page_spin_changed)
        toolbar.append(self._page_spin)

        self._page_total_label = Gtk.Label(label="/ 1")
        self._page_total_label.add_css_class("dim-label")
        toolbar.append(self._page_total_label)

        page_next = _icon_button(
            "go-next-symbolic", tr("Next page"), lambda *_: self._go_page(1)
        )
        toolbar.append(page_next)

        zoom_out = _icon_button(
            "zoom-out-symbolic",
            tr("Zoom out"),
            lambda *_: self._set_zoom(self._zoom / 1.15),
        )
        toolbar.append(zoom_out)

        self._zoom_label = Gtk.Label(label="100%")
        self._zoom_label.add_css_class("dim-label")
        toolbar.append(self._zoom_label)

        zoom_in = _icon_button(
            "zoom-in-symbolic",
            tr("Zoom in"),
            lambda *_: self._set_zoom(self._zoom * 1.15),
        )
        toolbar.append(zoom_in)

        fit_btn = _icon_button(
            "zoom-fit-best-symbolic",
            tr("Fit width"),
            lambda *_: self._set_zoom(1.0),
        )
        toolbar.append(fit_btn)

        open_btn = _icon_button(
            "document-open-symbolic",
            tr("Open externally"),
            lambda *_: self._open_external(),
        )
        toolbar.append(open_btn)

        self._loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._loading_box.set_valign(Gtk.Align.CENTER)
        self._loading_box.set_halign(Gtk.Align.CENTER)
        self._loading_spinner = Gtk.Spinner()
        self._loading_spinner.start()
        self._loading_label = Gtk.Label(label=tr("Loading PDF..."))
        self._loading_box.append(self._loading_spinner)
        self._loading_box.append(self._loading_label)

        self._message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._message_box.set_valign(Gtk.Align.CENTER)
        self._message_box.set_halign(Gtk.Align.CENTER)
        self._message_label = Gtk.Label()
        self._message_label.set_wrap(True)
        self._message_label.set_justify(Gtk.Justification.CENTER)
        self._message_action = Gtk.Button()
        self._message_action.add_css_class("suggested-action")
        self._message_action.set_visible(False)
        self._message_action.connect("clicked", self._on_message_action_clicked)
        self._message_box.append(self._message_label)
        self._message_box.append(self._message_action)

        self._document_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._document_box.set_halign(Gtk.Align.FILL)
        self._document_box.set_margin_top(18)
        self._document_box.set_margin_bottom(18)
        self._document_box.set_margin_start(18)
        self._document_box.set_margin_end(18)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_hexpand(True)
        self._scrolled.set_vexpand(True)
        self._scrolled.add_css_class("pdf-reader-scroll")
        self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_child(self._document_box)
        self._vadj = self._scrolled.get_vadjustment()
        self._vadj.connect("value-changed", self._on_scroll_value_changed)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(150)
        self._stack.add_named(self._loading_box, "loading")
        self._stack.add_named(self._message_box, "message")
        self._stack.add_named(self._scrolled, "viewer")

        self.append(toolbar)
        self.append(self._stack)
        self._show_loading(tr("Open a PDF to start reading"))

    # Public API

    def open_document(
        self,
        source: str,
        note_name: str | None = None,
        return_view: str = "editor",
    ) -> None:
        self._flush_state()
        self._note_name = note_name or self.app.current_note or ""
        self._source_ref = source
        self._return_view = return_view
        self._loading_source = None
        self._pdf_path = None
        self._page_count = 0
        self._page = 0
        self._page_widgets = []
        self._scroll_by_page = {}
        self._update_title()

        pdf_path = self._resolve_pdf_path(source)
        if pdf_path is None:
            return

        self._pdf_path = pdf_path
        self._page_count = self._get_pdf_page_count(pdf_path)
        if self._page_count <= 0:
            self._show_message(
                tr("Could not open PDF"),
                action_label=tr("Open externally"),
                action=self._open_external,
            )
            return

        state = self._load_state()
        self._page = self._clamp_page(self._parse_int(state.get("page"), 0))
        self._zoom = self._clamp_zoom(self._parse_float(state.get("zoom"), 1.0))
        self._scroll_by_page = self._parse_scroll_state(state.get("scroll_by_page", {}))
        self._update_title()
        self._update_controls()
        self._render_document()

    def get_title(self) -> str:
        return self._current_title

    # State

    def _state_key(self) -> str | None:
        if not self._note_name or not self._source_ref:
            return None
        return f"{self._note_name}::{self._source_ref}"

    def _load_state(self) -> dict[str, Any]:
        cfg = self._config_manager
        key = self._state_key()
        if cfg is None or key is None or not hasattr(cfg, "get_pdf_state"):
            return {}
        return cfg.get_pdf_state(key)

    @staticmethod
    def _parse_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _parse_float(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _flush_state(self) -> None:
        if self._pdf_path is None:
            return
        if self._pending_state_save:
            GLib.source_remove(self._pending_state_save)
            self._pending_state_save = 0
        self._save_current_scroll_ratio()
        self._save_state()

    def _save_state(self) -> None:
        cfg = self._config_manager
        key = self._state_key()
        if cfg is None or key is None or not hasattr(cfg, "set_pdf_state"):
            return
        cfg.set_pdf_state(
            key,
            {
                "page": self._page,
                "zoom": self._zoom,
                "scroll_by_page": {
                    str(page): ratio for page, ratio in self._scroll_by_page.items()
                },
            },
        )

    def _parse_scroll_state(self, raw: Any) -> dict[int, float]:
        result: dict[int, float] = {}
        if not isinstance(raw, dict):
            return result
        for key, value in raw.items():
            try:
                page = int(key)
                ratio = float(value)
            except (TypeError, ValueError):
                continue
            result[page] = max(0.0, min(1.0, ratio))
        return result

    # Document resolution

    def _resolve_pdf_path(self, source: str) -> Path | None:
        notes_dir = Path(self.app.notes_manager.notes_dir)
        if source.startswith(("http://", "https://")):
            cache_path = self._remote_document_cache_path(source, notes_dir)
            if cache_path.exists():
                return cache_path
            self._start_remote_download(source, cache_path)
            self._show_loading(tr("Downloading PDF..."))
            return None

        clean = source.split("#")[0].split("?")[0]
        resolved = resolve_image_path(notes_dir, clean)
        if resolved is None or not resolved.exists():
            self._show_message(
                tr("PDF not found"),
                action_label=tr("Open externally"),
                action=self._open_external,
            )
            return None
        return resolved

    def _remote_document_cache_path(self, url: str, notes_dir: Path) -> Path:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return notes_dir / ".documents" / f"remote_{url_hash}.pdf"

    def _start_remote_download(self, url: str, cache_path: Path) -> None:
        if self._download_thread and self._download_thread.is_alive():
            return
        self._loading_source = url

        def _download() -> None:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "TokyoNotes/1.0"}
                )
                with urlopen_with_fallback(req) as resp:
                    data = resp.read()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
                GLib.idle_add(self._on_remote_downloaded, url)
            except Exception:
                GLib.idle_add(self._on_remote_download_failed, url)

        self._download_thread = threading.Thread(target=_download, daemon=True)
        self._download_thread.start()

    def _on_remote_downloaded(self, url: str) -> bool:
        if url != self._loading_source:
            return False
        if self.app.content_stack.get_visible_child_name() != "pdf_reader":
            return False
        self._loading_source = None
        self._download_thread = None
        self.open_document(url, self._note_name, self._return_view)
        return False

    def _on_remote_download_failed(self, url: str) -> bool:
        if url != self._loading_source:
            return False
        if self.app.content_stack.get_visible_child_name() != "pdf_reader":
            return False
        self._loading_source = None
        self._download_thread = None
        self._show_message(
            tr("Failed to load PDF"),
            action_label=tr("Open externally"),
            action=self._open_external,
        )
        return False

    # Rendering

    def _get_pdf_page_count(self, pdf_path: Path) -> int:
        return self.app.editor._get_pdf_page_count(pdf_path)

    def _render_width(self) -> int:
        width = self._scrolled.get_allocated_width()
        if width <= 0:
            width = 960
        return max(400, int(width * self._zoom) - 24)

    def _clear_rendered_pages(self) -> None:
        child = self._document_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._document_box.remove(child)
            child = next_child
        self._page_widgets = []

    def _render_document(self) -> None:
        if self._pdf_path is None or self._page_count <= 0:
            return

        pdf_path = self._pdf_path
        page_count = self._page_count
        render_w = self._render_width()
        render_h = max(render_w * 4, 1200)
        self._clear_rendered_pages()
        self._render_generation += 1
        generation = self._render_generation

        # Reserve page space immediately. Rendering every page synchronously
        # made large PDFs block the GTK main loop and delayed the back button.
        for _page in range(page_count):
            page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            page_box.set_halign(Gtk.Align.CENTER)
            page_box.add_css_class("pdf-reader-page")
            page_box.set_size_request(render_w, render_h)
            self._document_box.append(page_box)
            self._page_widgets.append(page_box)

        self._stack.set_visible_child_name("viewer")
        self._update_title()
        self._update_controls()
        GLib.idle_add(self._scroll_to_page, self._page)

        def render_pages() -> None:
            for page in range(page_count):
                pixbuf = self.app.editor._render_pdf_pixbuf(
                    pdf_path, render_w, render_h, page
                )
                GLib.idle_add(self._replace_rendered_page, generation, page, pixbuf)

        threading.Thread(target=render_pages, daemon=True).start()

    def _replace_rendered_page(self, generation: int, page: int, pixbuf) -> bool:
        if generation != self._render_generation:
            return False
        page_box = self._page_widget(page)
        if page_box is None:
            return False
        if pixbuf is None:
            return False
        while child := page_box.get_first_child():
            page_box.remove(child)
        picture = Gtk.Picture.new_for_pixbuf(pixbuf)
        picture.set_halign(Gtk.Align.CENTER)
        picture.set_valign(Gtk.Align.START)
        picture.set_size_request(pixbuf.get_width(), pixbuf.get_height())
        page_box.set_size_request(pixbuf.get_width(), pixbuf.get_height())
        page_box.append(picture)
        return False

    def _show_loading(self, text: str) -> None:
        self._loading_label.set_label(text)
        self._stack.set_visible_child_name("loading")

    def _show_message(
        self,
        text: str,
        action_label: str | None = None,
        action=None,
    ) -> None:
        self._message_label.set_label(text)
        if action_label and action:
            self._message_action.set_label(action_label)
            self._message_action.set_visible(True)
            self._message_action_callback = action
        else:
            self._message_action_callback = None
            self._message_action.set_visible(False)
        self._stack.set_visible_child_name("message")

    def _update_title(self) -> None:
        if self._pdf_path is not None:
            display = self._pdf_path.name
        elif self._source_ref.startswith(("http://", "https://")):
            display = os.path.basename(urllib.parse.urlparse(self._source_ref).path)
        else:
            display = os.path.basename(self._source_ref)
        self._current_title = tr("PDF: {name}").format(name=display or tr("Document"))
        self._title_label.set_label(self._current_title)

    def _update_controls(self) -> None:
        self._updating_controls = True
        try:
            page_upper = max(self._page_count, 1)
            adj = self._page_spin.get_adjustment()
            adj.set_lower(1)
            adj.set_upper(page_upper)
            self._page_spin.set_adjustment(adj)
            self._page_spin.set_value(self._page + 1)
            self._page_total_label.set_label(tr("/ {total}").format(total=page_upper))
            self._zoom_label.set_label(f"{int(round(self._zoom * 100))}%")
        finally:
            self._updating_controls = False

    def _clamp_page(self, page: int) -> int:
        if self._page_count <= 0:
            return 0
        return max(0, min(self._page_count - 1, page))

    def _clamp_zoom(self, zoom: float) -> float:
        return max(0.5, min(3.0, zoom))

    def _save_current_scroll_ratio(self) -> None:
        if self._pdf_path is None or self._page_count <= 0:
            return
        visible_page = self._visible_page_from_scroll()
        if visible_page is not None:
            self._page = visible_page
        page_widget = self._page_widget(self._page)
        if page_widget is None:
            ratio = 0.0
        else:
            alloc = page_widget.get_allocation()
            page_height = max(1, alloc.height)
            ratio = (self._vadj.get_value() - alloc.y) / page_height
            ratio = max(0.0, min(1.0, ratio))
        self._scroll_by_page[self._page] = ratio

    def _page_widget(self, page: int) -> Gtk.Widget | None:
        if page < 0 or page >= len(self._page_widgets):
            return None
        return self._page_widgets[page]

    def _scroll_to_page(self, page: int) -> bool:
        if self._pdf_path is None:
            return False
        page = self._clamp_page(page)
        ratio = self._scroll_by_page.get(page, 0.0)
        page_widget = self._page_widget(page)
        if page_widget is None:
            return False
        alloc = page_widget.get_allocation()
        max_scroll = max(0.0, self._vadj.get_upper() - self._vadj.get_page_size())
        target = min(max_scroll, max(0.0, alloc.y + alloc.height * ratio))
        self._suspend_scroll_save = True
        try:
            self._vadj.set_value(target)
            self._page = page
            self._update_controls()
        finally:
            self._suspend_scroll_save = False
        return False

    @staticmethod
    def _nearest_page_for_viewport(
        page_bounds: list[tuple[float, float]], scroll_value: float, page_size: float
    ) -> int | None:
        if not page_bounds:
            return None
        viewport_center = scroll_value + page_size / 2
        distances = [
            abs((top + height / 2) - viewport_center) for top, height in page_bounds
        ]
        return min(range(len(distances)), key=distances.__getitem__)

    def _visible_page_from_scroll(self) -> int | None:
        page_bounds: list[tuple[float, float]] = []
        for widget in self._page_widgets:
            alloc = widget.get_allocation()
            page_bounds.append((float(alloc.y), float(alloc.height)))
        return self._nearest_page_for_viewport(
            page_bounds,
            self._vadj.get_value(),
            self._vadj.get_page_size(),
        )

    def _queue_state_save(self) -> None:
        if self._pending_state_save:
            GLib.source_remove(self._pending_state_save)
        self._pending_state_save = GLib.timeout_add(250, self._flush_state_idle)

    def _flush_state_idle(self) -> bool:
        self._pending_state_save = 0
        self._save_current_scroll_ratio()
        self._save_state()
        return False

    def _on_scroll_value_changed(self, adj: Gtk.Adjustment) -> None:
        if self._suspend_scroll_save:
            return
        visible_page = self._visible_page_from_scroll()
        if visible_page is not None and visible_page != self._page:
            self._page = visible_page
            self._update_controls()
        self._queue_state_save()

    # Controls

    def _go_page(self, delta: int) -> None:
        self._set_page(self._page + delta)

    def _set_page(self, page: int) -> None:
        if self._pdf_path is None:
            return
        page = self._clamp_page(page)
        if page == self._page:
            return
        self._save_current_scroll_ratio()
        self._page = page
        self._scroll_by_page[page] = 0.0
        self._scroll_to_page(page)
        self._save_state()

    def _on_page_spin_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating_controls:
            return
        self._set_page(int(spin.get_value()) - 1)

    def _set_zoom(self, zoom: float) -> None:
        if self._pdf_path is None:
            return
        zoom = self._clamp_zoom(zoom)
        if abs(zoom - self._zoom) < 0.001:
            return
        self._save_current_scroll_ratio()
        self._zoom = zoom
        self._render_document()
        self._save_state()

    def _open_external(self) -> None:
        if self._source_ref.startswith(("http://", "https://")):
            uri = self._source_ref
        elif self._pdf_path is not None:
            uri = self._pdf_path.as_uri()
        else:
            return
        try:
            root = self.get_root()
            Gtk.show_uri(root, uri, Gdk.CURRENT_TIME)
        except Exception:
            import webbrowser

            webbrowser.open_new_tab(uri)

    def _on_message_action_clicked(self, *_args) -> None:
        if self._message_action_callback is not None:
            self._message_action_callback()

    def _go_back(self) -> None:
        self._flush_state()
        if hasattr(self.app, "nav"):
            self.app.nav.on_escape_shortcut()
