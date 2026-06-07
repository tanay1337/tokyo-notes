"""Full-page flashcard review view — overview, card flip, session rating."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango

from core.flashcard import Flashcard, parse_note
from core.translations import tr
from core.utils import clear_listbox


class FlashcardReview(Gtk.Box):
    def __init__(
        self,
        get_notes_fn: Callable[[], list[str]],
        read_fn: Callable[[str], str],
        assets_dir: Path,
        on_note_selected: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._get_notes_fn = get_notes_fn
        self._read_fn = read_fn
        self._assets_dir = assets_dir
        self._on_note_selected = on_note_selected

        self._all_cards: list[Flashcard] = []
        self._queue: list[Flashcard] = []
        self._current_index: int = 0
        self._showing_answer: bool = False
        self._shuffle: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(200)
        self._stack.set_vexpand(True)

        self._build_overview_page()
        self._build_review_page()

        self.append(self._stack)

    def _build_overview_page(self) -> None:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=tr("Flashcards"), xalign=0)
        title.add_css_class("view-title")
        title.set_hexpand(True)
        header_box.append(title)

        review_all_btn = Gtk.Button(label=tr("Review All"))
        review_all_btn.add_css_class("suggested-action")
        review_all_btn.connect("clicked", lambda _: self._start_review(None))
        header_box.append(review_all_btn)

        page.append(header_box)

        self._notes_list = Gtk.ListBox()
        self._notes_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._notes_list.add_css_class("flashcard-note-list")
        page.append(self._notes_list)

        scrolled.set_child(page)
        self._stack.add_named(scrolled, "overview")

    def _build_review_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)

        # Top bar: back + progress
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        back_btn = Gtk.Button(label=tr("Back"))
        back_btn.connect("clicked", lambda _: self._show_overview())
        top_bar.append(back_btn)

        self._progress_label = Gtk.Label(xalign=0.5)
        self._progress_label.set_hexpand(True)
        top_bar.append(self._progress_label)

        shuffle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        shuffle_box.set_valign(Gtk.Align.CENTER)
        shuffle_label = Gtk.Label(label=tr("Shuffle"))
        shuffle_label.set_valign(Gtk.Align.CENTER)
        shuffle_box.append(shuffle_label)

        self._shuffle_switch = Gtk.Switch()
        self._shuffle_switch.set_valign(Gtk.Align.CENTER)
        self._shuffle_switch.set_active(self._shuffle)
        self._shuffle_switch.connect("notify::active", self._on_shuffle_toggled)
        shuffle_box.append(self._shuffle_switch)

        top_bar.append(shuffle_box)

        page.append(top_bar)

        # Card area
        card_frame = Gtk.Frame(hexpand=True, vexpand=True)
        card_frame.add_css_class("flashcard-card")
        card_frame.set_cursor_from_name("pointer")

        self._card_stack = Gtk.Stack()
        self._card_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._card_stack.set_transition_duration(300)

        self._front_label = Gtk.Label(
            xalign=0.5,
            yalign=Gtk.Align.CENTER,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        self._front_label.add_css_class("flashcard-text")
        self._front_label.set_margin_start(32)
        self._front_label.set_margin_end(32)
        self._front_label.set_margin_top(32)
        self._front_label.set_margin_bottom(32)

        self._back_label = Gtk.Label(
            xalign=0.5,
            yalign=Gtk.Align.CENTER,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        self._back_label.add_css_class("flashcard-text")
        self._back_label.set_margin_start(32)
        self._back_label.set_margin_end(32)
        self._back_label.set_margin_top(32)
        self._back_label.set_margin_bottom(32)

        self._done_label = Gtk.Label(
            xalign=0.5,
            yalign=Gtk.Align.CENTER,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        self._done_label.add_css_class("flashcard-done-label")
        self._done_label.set_margin_start(32)
        self._done_label.set_margin_end(32)
        self._done_label.set_margin_top(32)
        self._done_label.set_margin_bottom(32)

        self._card_stack.add_named(self._front_label, "front")
        self._card_stack.add_named(self._back_label, "back")
        self._card_stack.add_named(self._done_label, "done")
        self._card_stack.set_visible_child_name("front")

        card_frame.set_child(self._card_stack)

        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect("pressed", lambda *_: self._reveal_answer())
        card_frame.add_controller(click_gesture)

        page.append(card_frame)

        # Rating buttons
        self._review_btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )
        self._review_btn_box.set_halign(Gtk.Align.CENTER)

        self._again_btn = Gtk.Button(label=tr("Study Again"))
        self._again_btn.add_css_class("destructive-action")
        self._again_btn.add_css_class("flashcard-rate-btn")
        self._again_btn.connect("clicked", lambda _: self._rate_card(False))
        self._review_btn_box.append(self._again_btn)

        self._got_it_btn = Gtk.Button(label=tr("Got It"))
        self._got_it_btn.add_css_class("suggested-action")
        self._got_it_btn.add_css_class("flashcard-rate-btn")
        self._got_it_btn.connect("clicked", lambda _: self._rate_card(True))
        self._review_btn_box.append(self._got_it_btn)

        page.append(self._review_btn_box)

        # Done button (hidden until review complete)
        self._done_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._done_btn_box.set_halign(Gtk.Align.CENTER)

        overview_btn = Gtk.Button(label=tr("Back to Overview"))
        overview_btn.add_css_class("suggested-action")
        overview_btn.connect("clicked", lambda _: self._show_overview())
        self._done_btn_box.append(overview_btn)

        self._done_btn_box.set_visible(False)
        page.append(self._done_btn_box)

        self._stack.add_named(page, "review")

    def refresh(self) -> None:
        self._all_cards = []
        for note_name in self._get_notes_fn():
            content = self._read_fn(note_name)
            if content:
                self._all_cards.extend(parse_note(content, note_name))
        self._rebuild_overview()

    def _rebuild_overview(self) -> None:
        clear_listbox(self._notes_list)

        note_counts: dict[str, int] = {}
        for card in self._all_cards:
            note_counts[card.note_path] = note_counts.get(card.note_path, 0) + 1

        if not note_counts:
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
                halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
            )
            label = Gtk.Label(
                label=tr(
                    "No flashcards found.\n\n"
                    "Write ```flashcard blocks in your notes\n"
                    "to create flashcards."
                ),
                justify=Gtk.Justification.CENTER,
            )
            label.add_css_class("dim-label")
            box.append(label)
            empty_row = Gtk.ListBoxRow()
            empty_row.set_sensitive(False)
            empty_row.set_child(box)
            empty_row.set_margin_top(40)
            empty_row.set_margin_bottom(40)
            self._notes_list.append(empty_row)
            return

        for note_name, count in sorted(note_counts.items(), key=lambda x: -x[1]):
            row = Gtk.ListBoxRow()
            row.add_css_class("flashcard-note-row")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(10)
            box.set_margin_bottom(10)

            name_label = Gtk.Label(label=note_name, xalign=0)
            name_label.set_hexpand(True)
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(name_label)

            count_label = Gtk.Label(label=str(count))
            count_label.add_css_class("flashcard-count-badge")
            box.append(count_label)

            row.set_child(box)

            gesture = Gtk.GestureClick.new()
            gesture.connect("pressed", lambda *_, n=note_name: self._start_review(n))
            row.add_controller(gesture)

            self._notes_list.append(row)

    def _start_review(self, note_name: str | None) -> None:
        if note_name:
            self._queue = [c for c in self._all_cards if c.note_path == note_name]
        else:
            self._queue = list(self._all_cards)

        if not self._queue:
            return

        if self._shuffle:
            random.shuffle(self._queue)

        self._current_index = 0
        self._showing_answer = False
        self._review_btn_box.set_visible(True)
        self._done_btn_box.set_visible(False)
        self._show_current_card()
        self._stack.set_visible_child_name("review")

    def _show_current_card(self) -> None:
        if self._current_index >= len(self._queue):
            self._show_done()
            return

        card = self._queue[self._current_index]
        self._front_label.set_label(card.question)
        self._back_label.set_label(card.answer)
        self._card_stack.set_visible_child_name("front")
        self._showing_answer = False
        self._update_progress()

    def _reveal_answer(self) -> None:
        if self._showing_answer or self._current_index >= len(self._queue):
            return
        self._showing_answer = True
        self._card_stack.set_visible_child_name("back")

    def _rate_card(self, got_it: bool) -> None:
        if self._current_index >= len(self._queue):
            return

        if got_it:
            self._queue.pop(self._current_index)
        else:
            card = self._queue[self._current_index]
            self._queue.append(card)
            self._queue.pop(self._current_index)

        self._show_current_card()

    def _update_progress(self) -> None:
        self._progress_label.set_label(
            f"{len(self._queue) - self._current_index} of {len(self._queue)}"
        )

    def _show_done(self) -> None:
        self._done_label.set_label(tr("Review Complete"))
        self._card_stack.set_visible_child_name("done")
        self._showing_answer = False
        self._progress_label.set_label(tr("All done!"))
        self._review_btn_box.set_visible(False)
        self._done_btn_box.set_visible(True)

    def _on_shuffle_toggled(self, switch: Gtk.Switch, _pspec: object) -> None:
        self._shuffle = switch.get_active()

    def _show_overview(self) -> None:
        self.refresh()
        self._stack.set_visible_child_name("overview")
