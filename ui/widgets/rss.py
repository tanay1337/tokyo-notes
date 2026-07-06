from __future__ import annotations

import logging
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from core.translations import tr
from core.utils import urlopen_with_fallback
from ui.widgets.base import WidgetBase

logger = logging.getLogger(__name__)

try:
    import feedparser  # noqa: F401

    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False


def _parse_date(s: str) -> datetime | None:
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _fetch_feed(url: str) -> list[dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "TokyoNotes/1.0"})
    with urlopen_with_fallback(req, timeout=15) as resp:
        raw = resp.read()

    if HAS_FEEDPARSER:
        import feedparser

        parsed = feedparser.parse(raw)
        items = []
        for entry in parsed.entries[:20]:
            items.append(
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                }
            )
        return items

    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict[str, str]] = []

    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            items.append(
                {
                    "title": (item.findtext("title") or ""),
                    "link": (item.findtext("link") or ""),
                    "published": (item.findtext("pubDate") or ""),
                }
            )
    else:
        for entry in root.findall("atom:entry", ns):
            link_el = entry.find("atom:link", ns)
            href = link_el.get("href", "") if link_el is not None else ""
            items.append(
                {
                    "title": (entry.findtext("atom:title", "", ns) or ""),
                    "link": href,
                    "published": (entry.findtext("atom:published", "", ns) or ""),
                }
            )

    return items[:20]


class RSSWidget(WidgetBase):
    widget_type = "rss"
    widget_title = "Feed Reader"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timer_id: int | None = None
        self._fetched = False

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._content.append(self._list)

        self._status_label = Gtk.Label(label=tr("Add RSS feeds in widget settings"))
        self._status_label.set_halign(Gtk.Align.CENTER)
        self._status_label.set_margin_top(12)
        self._status_label.set_margin_bottom(12)
        self._list.append(self._status_label)

        self.connect("map", lambda *a: self._on_map())

    def _on_map(self) -> None:
        if not self._fetched:
            self._fetched = True
            GLib.idle_add(self._fetch_all)

    def get_config_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        feeds_label = Gtk.Label(label=tr("Feed URLs (one per line):"), xalign=0)
        box.append(feeds_label)

        self._cfg_feeds_buffer = Gtk.TextBuffer()
        self._cfg_feeds_buffer.set_text("\n".join(self.settings.get("feeds", [])))
        text_view = Gtk.TextView(buffer=self._cfg_feeds_buffer)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_size_request(-1, 100)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(text_view)
        scrolled.set_min_content_height(100)
        box.append(scrolled)

        max_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        max_label = Gtk.Label(label=tr("Max items:"))
        self._cfg_max_spin = Gtk.SpinButton.new_with_range(1, 50, 1)
        self._cfg_max_spin.set_value(self.settings.get("max_items", 10))
        max_row.append(max_label)
        max_row.append(self._cfg_max_spin)
        box.append(max_row)

        return box

    def apply_config(self) -> None:
        buf = self._cfg_feeds_buffer
        feeds = [
            line.strip()
            for line in buf.get_text(
                buf.get_start_iter(), buf.get_end_iter(), False
            ).split("\n")
            if line.strip()
        ]
        self.settings["feeds"] = feeds
        self.settings["max_items"] = int(self._cfg_max_spin.get_value())
        self._fetch_all()

    def _fetch_all(self) -> None:
        urls = self.settings.get("feeds", [])
        max_items = self.settings.get("max_items", 10)

        while (child := self._list.get_first_child()) is not None:
            self._list.remove(child)

        if not urls:
            self._list.append(self._status_label)
            return

        threading.Thread(
            target=self._fetch_all_worker, args=(urls, max_items), daemon=True
        ).start()

    def _fetch_all_worker(self, urls: list[str], max_items: int) -> None:
        all_items: list[tuple[str, str, str, str]] = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            fut_map = {pool.submit(_fetch_feed, url): url for url in urls}
            for future in as_completed(fut_map):
                url = fut_map[future]
                try:
                    items = future.result()
                    domain = urllib.parse.urlparse(url).netloc
                    for it in items:
                        all_items.append(
                            (it["title"], it["link"], domain, it["published"])
                        )
                except Exception as e:
                    logger.warning("RSS fetch failed for %s: %s", url, e)

        all_items.sort(
            key=lambda x: (
                _parse_date(x[3]) or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )
        GLib.idle_add(lambda: self._populate_fetched(all_items[:max_items]))

    def _populate_fetched(self, items: list[tuple[str, str, str, str]]) -> None:
        for title, link, domain, _published in items:
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(4)
            box.set_margin_bottom(4)
            box.set_margin_start(6)
            box.set_margin_end(6)

            title_lbl = Gtk.Label(label=title, xalign=0)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_hexpand(True)

            domain_lbl = Gtk.Label(label=domain, xalign=0)
            domain_lbl.add_css_class("dim-label")
            domain_lbl.set_halign(Gtk.Align.START)

            box.append(title_lbl)
            box.append(domain_lbl)
            row.set_child(box)

            if link:
                gesture = Gtk.GestureClick.new()
                gesture.connect(
                    "pressed",
                    lambda *a, _l=link: Gtk.show_uri(None, _l, Gdk.CURRENT_TIME),
                )
                row.add_controller(gesture)
                row.set_cursor_from_name("pointer")

            self._list.append(row)

    def update_periodic(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._fetch_all()
        interval = max(self.settings.get("interval_min", 15), 5) * 60 * 1000
        self._timer_id = GLib.timeout_add(interval, self._fetch_all)
