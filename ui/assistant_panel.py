"""User-controlled chat and document-assistance panel."""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from core.ai import (
    AIProviderError,
    CancelToken,
    ChatRequest,
    GenerationCancelled,
    LlamaCppProvider,
    is_loopback_url,
)
from core.assistant import (
    SYSTEM_INSTRUCTIONS,
    ChatHistoryStore,
    ChatThread,
    ContextAttachment,
    EditProposal,
    StoredMessage,
    build_messages,
    parse_flashcards,
)
from core.translations import tr

_ACTION_PROMPTS = {
    "ask": "",
    "summary": (
        "Write a concise Markdown summary of the reference context. Preserve key "
        "facts, decisions, and action items. Return only the proposed Markdown."
    ),
    "flashcards": (
        "Create useful study flashcards from the reference context. Return only "
        "one or more valid ```flashcard fences, each with question, --- and answer."
    ),
    "cleanup": (
        "Clean up the target text for clarity and consistent Markdown without "
        "changing its meaning. Return only the complete replacement Markdown."
    ),
    "janitor": (
        "Audit the selected documents. Group concise, actionable findings by note. "
        "Do not rewrite anything and do not invent problems."
    ),
}

_ACTION_LABELS = {
    "summary": "Summarize",
    "flashcards": "Flashcards",
    "cleanup": "Clean up",
    "janitor": "Janitor",
}


class _TextReviewWindow(Adw.Window):
    """Scrollable review surface for large outbound payloads and diffs."""

    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        text: str,
        confirm_label: str,
        on_confirm,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(title)
        self.set_default_size(760, 640)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=title))
        layout.append(header)

        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_top_margin(12)
        view.set_bottom_margin(12)
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.get_buffer().set_text(text)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(view)
        layout.append(scroll)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        buttons.set_margin_bottom(12)
        buttons.set_margin_end(12)
        cancel = Gtk.Button(label=tr("Cancel"))
        cancel.connect("clicked", lambda _b: self.close())
        buttons.append(cancel)
        confirm = Gtk.Button(label=confirm_label)
        confirm.add_css_class("suggested-action")

        def confirmed(_button) -> None:
            self.close()
            on_confirm()

        confirm.connect("clicked", confirmed)
        buttons.append(confirm)
        layout.append(buttons)
        self.set_content(layout)


class AssistantPanel(Gtk.Box):
    """A side panel whose model output is inert until the user applies it."""

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        self.set_size_request(300, -1)
        self.add_css_class("assistant-panel")
        self.set_margin_top(8)
        self.set_margin_bottom(8)

        self.history = ChatHistoryStore(Path(app.notes_folder))
        self.thread = ChatThread()
        self.attachments: list[ContextAttachment] = []
        self.cancel_token: CancelToken | None = None
        self._generation_serial = 0
        self._active_prompt = ""
        self._active_pieces: list[str] = []
        self._active_reply: Gtk.Label | None = None
        self._active_mode = "ask"
        self._active_model = ""
        self._model_ids: list[str] = []
        self._models_loading = False
        self._all_public_context = False
        self.pending_proposal: EditProposal | None = None
        self._proposal_mode = "ask"
        self._proposal_selection: tuple[int, int] | None = None
        self._undo: tuple[str, str, str] | None = None
        self._last_output = ""

        self._build_header()
        self._build_messages()
        self._build_composer()
        self.new_chat()
        self.refresh_models()

    def _build_header(self) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=tr("Assistant"), xalign=0)
        title.add_css_class("title-3")
        title.set_hexpand(True)
        row.append(title)

        history_btn = Gtk.Button(icon_name="document-open-symbolic")
        history_btn.add_css_class("flat")
        history_btn.set_tooltip_text(tr("Chat history"))
        history_btn.connect("clicked", self._show_history)
        row.append(history_btn)
        new_btn = Gtk.Button(icon_name="document-new-symbolic")
        new_btn.add_css_class("flat")
        new_btn.set_tooltip_text(tr("New chat"))
        new_btn.connect("clicked", lambda _b: self.new_chat())
        row.append(new_btn)
        self.append(row)

    def _build_notes_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)

        selection_btn = Gtk.Button(label=tr("Attach selection"))
        selection_btn.connect("clicked", self._attach_selection)
        outer.append(selection_btn)
        note_btn = Gtk.Button(label=tr("Attach current note"))
        note_btn.connect("clicked", self._attach_current_note)
        outer.append(note_btn)
        outer.append(Gtk.Separator())

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(240)
        scroll.set_min_content_width(260)
        listing = Gtk.ListBox()
        folders = self.app.notes_manager.get_folders()
        if folders:
            folder_header = Gtk.ListBoxRow(selectable=False, activatable=False)
            folder_header.set_child(Gtk.Label(label=tr("Folders"), xalign=0))
            listing.append(folder_header)
            for folder in folders:
                row = Gtk.ListBoxRow()
                check = Gtk.CheckButton(label=f"📁 {folder}")
                check.connect("toggled", self._toggle_folder_attachment, folder)
                row.set_child(check)
                listing.append(row)
            note_header = Gtk.ListBoxRow(selectable=False, activatable=False)
            note_header.set_child(Gtk.Label(label=tr("Notes"), xalign=0))
            listing.append(note_header)
        for name in self.app.notes_manager.get_notes():
            row = Gtk.ListBoxRow()
            check = Gtk.CheckButton(label=name)
            check.set_active(any(a.note_name == name for a in self.attachments))
            check.connect("toggled", self._toggle_note_attachment, name)
            row.set_child(check)
            listing.append(row)
        scroll.set_child(listing)
        outer.append(scroll)
        popover.set_child(outer)
        return popover

    def _toggle_folder_attachment(self, check: Gtk.CheckButton, folder: str) -> None:
        names = self.app.notes_manager.get_notes_in_folder(folder)
        if not check.get_active():
            self._all_public_context = False
            name_set = set(names)
            self.attachments = [
                item for item in self.attachments if item.note_name not in name_set
            ]
            self._refresh_context_chips()
            return
        skipped = 0
        for name in names:
            if self.app.notes_manager.is_encrypted(name):
                if name != self.app.current_note or self.app._is_session_locked:
                    skipped += 1
                    continue
                content = self._buffer_text()
                encrypted = True
            else:
                content = self.app.notes_manager.read_plain(name)
                encrypted = False
            self._replace_attachment(
                ContextAttachment.create("note", name, content, name, encrypted)
            )
        if skipped:
            self.app._show_toast(
                tr("Skipped {n} locked private note(s)").format(n=skipped)
            )

    def _build_messages(self) -> None:
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.message_box.set_margin_top(4)
        self.message_box.set_margin_bottom(4)
        self.empty_state = Gtk.Label(
            label=tr(
                "Ask anything about your notes. All public notes are included by "
                "default."
            ),
            xalign=0,
        )
        self.empty_state.set_wrap(True)
        self.empty_state.add_css_class("dim-label")
        self.message_box.append(self.empty_state)
        scroll.set_child(self.message_box)
        self.message_scroll = scroll
        self.append(scroll)

    def _build_composer(self) -> None:
        self.apply_btn = Gtk.Button(label=tr("Review and apply proposal"))
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_visible(False)
        self.apply_btn.connect("clicked", self._review_proposal)
        self.append(self.apply_btn)

        output_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        output_actions.set_halign(Gtk.Align.END)
        self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.set_tooltip_text(tr("Copy response"))
        self.copy_btn.set_visible(False)
        self.copy_btn.connect("clicked", self._copy_output)
        output_actions.append(self.copy_btn)
        self.summary_note_btn = Gtk.Button(icon_name="document-new-symbolic")
        self.summary_note_btn.add_css_class("flat")
        self.summary_note_btn.set_tooltip_text(tr("Create summary note"))
        self.summary_note_btn.set_visible(False)
        self.summary_note_btn.connect("clicked", self._create_summary_note)
        output_actions.append(self.summary_note_btn)
        self.append(output_actions)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.undo_btn.add_css_class("flat")
        self.undo_btn.set_halign(Gtk.Align.END)
        self.undo_btn.set_tooltip_text(tr("Undo AI change"))
        self.undo_btn.set_visible(False)
        self.undo_btn.connect("clicked", self._undo_ai_change)
        self.append(self.undo_btn)

        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        composer.add_css_class("assistant-composer")

        self.prompt = Gtk.TextView()
        self.prompt.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt.set_top_margin(8)
        self.prompt.set_bottom_margin(8)
        self.prompt.set_left_margin(8)
        self.prompt.set_right_margin(8)
        composer_scroll = Gtk.ScrolledWindow()
        composer_scroll.set_min_content_height(58)
        composer_scroll.set_max_content_height(116)
        composer_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        composer_scroll.set_child(self.prompt)
        composer.append(composer_scroll)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_prompt_key_pressed)
        self.prompt.add_controller(key_controller)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        configured = self.app.cfg.get("llama_cpp_model", "")
        initial_models = [configured] if configured else [tr("Discovering…")]
        self.model_dropdown = Gtk.DropDown.new_from_strings(initial_models)
        self.model_dropdown.set_size_request(94, -1)
        self.model_dropdown.add_css_class("assistant-model")
        self.model_dropdown.set_sensitive(bool(configured))
        self.model_dropdown.set_tooltip_text(tr("Local llama.cpp model"))
        self.model_dropdown.connect("notify::selected", self._on_model_selected)
        if configured:
            self._model_ids = [configured]
        row.append(self.model_dropdown)

        self.context_menu = Gtk.MenuButton()
        self.context_menu.add_css_class("flat")
        self.context_menu.set_child(
            Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
        )
        self.context_menu.set_tooltip_text(tr("Context"))
        self.context_menu.set_popover(self._build_notes_popover())
        row.append(self.context_menu)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)

        self.send_btn = Gtk.Button(icon_name="go-up-symbolic")
        self.send_btn.add_css_class("suggested-action")
        self.send_btn.add_css_class("circular")
        self.send_btn.set_tooltip_text(tr("Send"))
        self.send_btn.connect("clicked", self._send_or_stop)
        row.append(self.send_btn)
        composer.append(row)
        self.append(composer)

    def refresh_models(self) -> None:
        """Discover loaded llama.cpp models without sending note content."""
        if self._models_loading:
            return
        try:
            url = self._server_url()
        except (TypeError, ValueError) as exc:
            self.model_dropdown.set_tooltip_text(str(exc))
            return
        if not is_loopback_url(url):
            self.model_dropdown.set_tooltip_text(tr("Use a localhost URL"))
            return
        self._models_loading = True
        self.model_dropdown.set_sensitive(False)

        def worker() -> None:
            try:
                models = LlamaCppProvider(
                    url, api_key=self.app.cfg.get("llama_cpp_api_key", "")
                ).list_models()
                GLib.idle_add(self._finish_model_refresh, models, "")
            except Exception:
                GLib.idle_add(self._finish_model_refresh, [], tr("Could not connect"))

        threading.Thread(target=worker, name="assistant-models", daemon=True).start()

    def _finish_model_refresh(self, models: list[str], error: str) -> bool:
        self._models_loading = False
        if error:
            configured = self.app.cfg.get("llama_cpp_model", "")
            self._model_ids = [configured] if configured else []
            labels = self._model_ids or [tr("No local model")]
            self.model_dropdown.set_model(Gtk.StringList.new(labels))
            self.model_dropdown.set_sensitive(bool(self._model_ids))
            self.model_dropdown.set_tooltip_text(error)
            return False
        self._model_ids = models
        self.model_dropdown.set_model(
            Gtk.StringList.new(models or [tr("No local model")])
        )
        self.model_dropdown.set_sensitive(bool(models))
        self.model_dropdown.set_tooltip_text(
            tr("Local llama.cpp model") if models else tr("No loaded models found")
        )
        if models:
            configured = self.app.cfg.get("llama_cpp_model", "")
            selected = models.index(configured) if configured in models else 0
            self.model_dropdown.set_selected(selected)
            self.app.cfg.set("llama_cpp_model", models[selected])
        return False

    def _on_model_selected(self, dropdown: Gtk.DropDown, _param) -> None:
        selected = dropdown.get_selected()
        if 0 <= selected < len(self._model_ids):
            model = self._model_ids[selected]
            self.app.cfg.set("llama_cpp_model", model)
            self.thread.model = model

    def _archive_active_generation(self) -> str:
        """Preserve partial output before leaving a generating conversation."""
        token = self.cancel_token
        if token is None:
            return ""
        self._generation_serial += 1
        token.cancel()
        self.cancel_token = None
        output = "".join(self._active_pieces).strip() or tr("Stopped")
        if self._active_reply is not None:
            self._active_reply.remove_css_class("assistant-generating")
            self._active_reply.set_markup(self._markdown_markup(output))
        self._store_exchange(self._active_prompt, output, self._active_model)
        self._clear_active_generation()
        return output

    def _attach_public_notes(self) -> None:
        """Use every non-private note as the default reference context."""
        items: list[ContextAttachment] = []
        for name in self.app.notes_manager.get_notes():
            if self.app.notes_manager.is_encrypted(name):
                continue
            try:
                content = self.app.notes_manager.read_plain(name)
            except OSError:
                continue
            items.append(ContextAttachment.create("note", name, content, name))
        self.attachments = items
        self._all_public_context = True

    def _clear_active_generation(self) -> None:
        self._active_prompt = ""
        self._active_pieces = []
        self._active_reply = None
        self._active_mode = "ask"
        self._active_model = ""

    def new_chat(self) -> None:
        self._archive_active_generation()
        self._generation_serial += 1
        self.thread = ChatThread(provider="llama_cpp")
        self._attach_public_notes()
        self.pending_proposal = None
        self._last_output = ""
        self.apply_btn.set_visible(False)
        self.copy_btn.set_visible(False)
        self.summary_note_btn.set_visible(False)
        self._clear_box(self.message_box)
        self.message_box.append(self.empty_state)
        self._refresh_context_chips()
        self._set_generating(False)

    @staticmethod
    def _clear_box(box: Gtk.Box | Gtk.FlowBox) -> None:
        child = box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _add_message(self, role: str, content: str) -> Gtk.Label:
        if self.empty_state.get_parent() is self.message_box:
            self.message_box.remove(self.empty_state)
        label = Gtk.Label(xalign=0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.add_css_class("assistant-user" if role == "user" else "assistant-reply")
        if role == "user":
            label.set_text(content)
        else:
            label.set_markup(self._markdown_markup(content))
        if role == "user":
            label.set_halign(Gtk.Align.END)
            label.set_max_width_chars(34)
        self.message_box.append(label)
        GLib.idle_add(self._scroll_to_bottom)
        return label

    def _scroll_to_bottom(self) -> bool:
        adjustment = self.message_scroll.get_vadjustment()
        adjustment.set_value(
            max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        )
        return False

    @staticmethod
    def _markdown_markup(text: str) -> str:
        """Render common Markdown safely using the GTK label's Pango markup."""
        rendered: list[str] = []
        for line in text.splitlines() or [""]:
            heading = re.match(r"^#{1,6}\s+(.*)$", line)
            body = heading.group(1) if heading else line
            escaped = GLib.markup_escape_text(body)
            escaped = re.sub(
                r"`([^`]+)`", r'<span font_family="monospace">\1</span>', escaped
            )
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
            escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escaped)
            if heading:
                escaped = f"<b>{escaped}</b>"
            if re.match(r"^[-*+]\s+", line):
                escaped = re.sub(r"^[-*+]\s+", "• ", escaped)
            rendered.append(escaped)
        return "\n".join(rendered)

    def _buffer_text(self) -> str:
        start, end = self.app.buffer.get_bounds()
        return self.app.buffer.get_text(start, end, True)

    def _attach_selection(self, _button) -> None:
        try:
            start, end = self.app.buffer.get_selection_bounds()
        except ValueError:
            self.app._show_toast(tr("Select text first"))
            return
        content = self.app.buffer.get_text(start, end, True)
        if not content:
            self.app._show_toast(tr("Select text first"))
            return
        note = self.app.current_note
        encrypted = bool(note and self.app.notes_manager.is_encrypted(note))
        item = ContextAttachment.create(
            "selection", tr("Selection"), content, note, encrypted
        )
        self._replace_attachment(item)

    def _attach_current_note(self, _button) -> None:
        name = self.app.current_note
        if not name or name.startswith(".template:"):
            return
        encrypted = self.app.notes_manager.is_encrypted(name)
        if encrypted and self.app._is_session_locked:
            self.app._show_toast(tr("Unlock the private note first"))
            return
        item = ContextAttachment.create(
            "note", name, self._buffer_text(), name, encrypted
        )
        self._replace_attachment(item)

    def _toggle_note_attachment(self, check: Gtk.CheckButton, name: str) -> None:
        if not check.get_active():
            self._all_public_context = False
            self.attachments = [a for a in self.attachments if a.note_name != name]
            self._refresh_context_chips()
            return
        if self.app.notes_manager.is_encrypted(name):
            if name != self.app.current_note or self.app._is_session_locked:
                check.set_active(False)
                self.app._show_toast(tr("Open and unlock the private note first"))
                return
            content = self._buffer_text()
            encrypted = True
        else:
            content = self.app.notes_manager.read_plain(name)
            encrypted = False
        self._replace_attachment(
            ContextAttachment.create("note", name, content, name, encrypted)
        )

    def _replace_attachment(self, item: ContextAttachment) -> None:
        key = (item.kind, item.note_name)
        self.attachments = [a for a in self.attachments if (a.kind, a.note_name) != key]
        self.attachments.append(item)
        if item.encrypted:
            self._all_public_context = False
        if item.encrypted:
            self.thread.ephemeral = True
        self._refresh_context_chips()

    def _refresh_context_chips(self) -> None:
        # Context is intentionally invisible in the chat surface. The paperclip
        # popover remains the explicit control for adding/removing references.
        return

    def _remove_attachment(self, _button, item: ContextAttachment) -> None:
        self._all_public_context = False
        self.attachments = [a for a in self.attachments if a is not item]
        self._refresh_context_chips()

    def _clear_public_context(self, _button) -> None:
        self._all_public_context = False
        self.attachments = [a for a in self.attachments if a.encrypted]
        self._refresh_context_chips()

    def _prompt_text(self) -> str:
        buf = self.prompt.get_buffer()
        return buf.get_text(*buf.get_bounds(), True).strip()

    def _on_prompt_key_pressed(
        self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType
    ) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not (
            state & Gdk.ModifierType.SHIFT_MASK
        ):
            self._send_or_stop(None)
            return True
        return False

    def _send_or_stop(self, _button) -> None:
        if self.cancel_token is not None:
            self._stop(None)
            return
        typed_prompt = self._prompt_text()
        # Advanced document operations remain available as explicit slash
        # commands, without turning the composer into a task selector.
        mode = "ask"
        prompt = typed_prompt
        for command in ("summary", "flashcards", "cleanup", "janitor"):
            prefix = f"/{command}"
            if typed_prompt.lower().startswith(prefix):
                mode = command
                prompt = typed_prompt[len(prefix) :].strip()
                break
        if mode == "ask":
            self._proposal_mode = "ask"
            self._start_request(prompt)
        else:
            self._run_action(mode, prompt)

    def _run_action(self, mode: str, typed_prompt: str = "") -> None:
        if mode != "janitor" and not self.attachments:
            self._attach_current_note(None)
        if mode == "janitor" and not self.attachments:
            self.app._show_toast(tr("Choose notes to review first"))
            return
        self._proposal_mode = mode
        self._proposal_selection = None
        if mode == "cleanup":
            try:
                start, end = self.app.buffer.get_selection_bounds()
                self._proposal_selection = (start.get_offset(), end.get_offset())
            except ValueError:
                pass
        prompt = _ACTION_PROMPTS[mode]
        if typed_prompt:
            prompt += f"\n\nAdditional user instruction:\n{typed_prompt}"
        display_prompt = tr(_ACTION_LABELS[mode])
        if typed_prompt:
            display_prompt += f" — {typed_prompt}"
        self._start_request(prompt, display_prompt)

    def _provider_and_model(self):
        local_url = self._server_url()
        if not is_loopback_url(local_url):
            raise ValueError(tr("llama.cpp URL must use localhost"))
        selected = self.model_dropdown.get_selected()
        model = (
            self._model_ids[selected]
            if 0 <= selected < len(self._model_ids)
            else self.app.cfg.get("llama_cpp_model", "")
        )
        return LlamaCppProvider(
            local_url, api_key=self.app.cfg.get("llama_cpp_api_key", "")
        ), model

    def _server_url(self) -> str:
        return self.app.cfg.get("llama_cpp_url", "http://127.0.0.1:8080/v1")

    def _start_request(self, prompt: str, display_prompt: str | None = None) -> None:
        if not prompt or self.cancel_token is not None:
            return
        if not self._refresh_note_attachments():
            return
        try:
            provider, model = self._provider_and_model()
        except ValueError as exc:
            self.app._show_toast(str(exc))
            return
        if not model:
            self.app._show_toast(tr("Choose a model in Settings first"))
            return
        request = ChatRequest(
            model=model,
            messages=build_messages(self.thread.messages, prompt, self.attachments),
            instructions=SYSTEM_INSTRUCTIONS,
        )
        self._begin_stream(request, display_prompt or prompt, provider)

    def _refresh_note_attachments(self) -> bool:
        """Read attached notes at send time so the visible chips are truthful."""
        refreshed: list[ContextAttachment] = []
        for item in self.attachments:
            if item.kind != "note" or not item.note_name:
                refreshed.append(item)
                continue
            name = item.note_name
            if self.app.notes_manager.is_encrypted(name):
                if name != self.app.current_note or self.app._is_session_locked:
                    self.app._show_toast(tr("Open and unlock the private note first"))
                    return False
                content = self._buffer_text()
                encrypted = True
            else:
                content = self.app.notes_manager.read_plain(name)
                encrypted = False
            refreshed.append(
                ContextAttachment.create("note", name, content, name, encrypted)
            )
        self.attachments = refreshed
        self._refresh_context_chips()
        return True

    def _begin_stream(self, request: ChatRequest, stored_prompt: str, provider) -> None:
        self._generation_serial += 1
        serial = self._generation_serial
        token = CancelToken()
        self.cancel_token = token
        self._set_generating(True)
        self.apply_btn.set_visible(False)
        self._add_message("user", stored_prompt)
        reply = self._add_message("assistant", tr("Generating…"))
        reply.add_css_class("assistant-generating")
        pieces: list[str] = []
        self._active_prompt = stored_prompt
        self._active_pieces = pieces
        self._active_reply = reply
        self._active_mode = self._proposal_mode
        self._active_model = request.model
        self.prompt.get_buffer().set_text("")

        def worker() -> None:
            try:
                for delta in provider.stream_chat(request, token):
                    pieces.append(delta)
                    GLib.idle_add(
                        self._show_stream_output,
                        serial,
                        reply,
                        "".join(pieces),
                    )
                GLib.idle_add(
                    self._stream_done,
                    serial,
                    token,
                    stored_prompt,
                    "".join(pieces),
                    None,
                )
            except GenerationCancelled:
                GLib.idle_add(
                    self._stream_done,
                    serial,
                    token,
                    stored_prompt,
                    "".join(pieces),
                    "stopped",
                )
            except AIProviderError as exc:
                GLib.idle_add(
                    self._stream_done,
                    serial,
                    token,
                    stored_prompt,
                    "".join(pieces),
                    str(exc),
                )
            except Exception:
                GLib.idle_add(
                    self._stream_done,
                    serial,
                    token,
                    stored_prompt,
                    "".join(pieces),
                    tr("Unexpected provider error"),
                )

        threading.Thread(target=worker, name="assistant-stream", daemon=True).start()

    def _show_stream_output(self, serial: int, reply: Gtk.Label, output: str) -> bool:
        if serial != self._generation_serial:
            return False
        reply.remove_css_class("assistant-generating")
        reply.set_markup(self._markdown_markup(output))
        GLib.idle_add(self._scroll_to_bottom)
        return False

    def _stream_done(
        self,
        serial: int,
        token: CancelToken,
        prompt: str,
        output: str,
        error: str | None,
    ) -> bool:
        if serial != self._generation_serial:
            return False
        if self.cancel_token is token:
            self.cancel_token = None
        self._set_generating(False)
        reply = self._active_reply
        if reply is not None:
            reply.remove_css_class("assistant-generating")
        if error == "stopped":
            output = output.strip() or tr("Stopped")
            if reply is not None:
                reply.set_markup(self._markdown_markup(output))
        elif error:
            if reply is not None and not output:
                reply.set_label(tr("Could not generate a response"))
            self.app._show_toast(error)
            self._clear_active_generation()
            return False
        self._store_exchange(prompt, output, self._active_model)
        mode = self._active_mode
        self._clear_active_generation()
        self._last_output = output if error != "stopped" else output.strip()
        self.copy_btn.set_visible(bool(self._last_output))
        self.summary_note_btn.set_visible(
            mode == "summary" and not self.thread.ephemeral and error is None
        )
        self.prompt.get_buffer().set_text("")
        if error is None and mode in ("summary", "flashcards", "cleanup"):
            self._proposal_mode = mode
            self._prepare_proposal(output)
        return False

    def _store_exchange(self, prompt: str, output: str, model: str) -> None:
        if not prompt:
            return
        self.thread.model = model
        self.thread.messages.extend(
            [StoredMessage("user", prompt), StoredMessage("assistant", output)]
        )
        self.thread.attachment_names = [
            a.note_name for a in self.attachments if a.note_name
        ]
        if self.thread.title == "New chat":
            self.thread.title = prompt[:50].strip() or tr("Assistant chat")
        self.history.save(self.thread)

    def _set_generating(self, active: bool) -> None:
        icon = "media-playback-stop-symbolic" if active else "go-up-symbolic"
        self.send_btn.set_icon_name(icon)
        self.send_btn.set_tooltip_text(tr("Stop") if active else tr("Send"))
        if active:
            self.send_btn.add_css_class("destructive-action")
        else:
            self.send_btn.remove_css_class("destructive-action")

    def _copy_output(self, _button) -> None:
        if not self._last_output:
            return
        provider = Gdk.ContentProvider.new_for_value(self._last_output)
        self.app.win.get_clipboard().set_content(provider)
        self.app._show_toast(tr("Assistant response copied"))

    def _create_summary_note(self, _button) -> None:
        if not self._last_output or not self.app.current_note:
            return
        source = Path(self.app.current_note)
        base = f"{source.name} Summary"
        candidate = str(source.parent / base) if str(source.parent) != "." else base
        name = self.app.notes_manager.reserve_name(candidate)

        def create() -> None:
            self.app.notes_manager.save_note(name, self._last_output + "\n")
            self.app.refresh_list(self.app.sidebar.search_entry.get_text())
            self.app._show_toast(tr("Created summary note '{name}'").format(name=name))

        dialog = _TextReviewWindow(
            self.app.win,
            tr("Create summary note?"),
            f"{name}.md\n\n{self._last_output}",
            tr("Create"),
            create,
        )
        dialog.present()

    def _prepare_proposal(self, output: str) -> None:
        note = self.app.current_note
        if not note or note.startswith(".template:"):
            return
        current = self._buffer_text()
        generated = output.strip()
        if self._proposal_mode == "flashcards":
            generated = parse_flashcards(output) or ""
            if not generated:
                self.app._show_toast(tr("The model did not return valid flashcards"))
                return
            operation = "insert"
            start = end = len(current)
            generated = ("\n\n" if current.strip() else "") + generated + "\n"
        elif self._proposal_mode == "summary":
            operation = "insert"
            insert = self.app.buffer.get_iter_at_mark(self.app.buffer.get_insert())
            start = end = insert.get_offset()
            generated = generated + "\n"
        elif self._proposal_selection:
            operation = "replace_range"
            start, end = self._proposal_selection
        else:
            operation = "replace_note"
            start, end = 0, len(current)
        self.pending_proposal = EditProposal(
            operation,
            note,
            hashlib.sha256(current.encode("utf-8")).hexdigest(),
            generated,
            start,
            end,
        )
        self.apply_btn.set_visible(True)

    def _review_proposal(self, _button) -> None:
        proposal = self.pending_proposal
        if proposal is None:
            return
        current = self._buffer_text()
        if proposal.target_note != self.app.current_note or not proposal.is_fresh(
            current
        ):
            self.pending_proposal = None
            self.apply_btn.set_visible(False)
            self.app._show_toast(tr("The note changed; generate a new proposal"))
            return
        before = current[proposal.start_offset : proposal.end_offset]
        body = (
            f"{tr('BEFORE')}\n{before}\n\n"
            f"{tr('PROPOSED')}\n{proposal.generated_markdown}"
        )
        dialog = _TextReviewWindow(
            self.app.win,
            tr("Review proposed change"),
            body,
            tr("Apply"),
            lambda: self._apply_proposal(proposal),
        )
        dialog.present()

    def _apply_proposal(self, proposal: EditProposal) -> None:
        current = self._buffer_text()
        if not proposal.is_fresh(current) or (
            proposal.target_note != self.app.current_note
        ):
            self.app._show_toast(tr("The note changed; proposal was not applied"))
            return
        updated = (
            current[: proposal.start_offset]
            + proposal.generated_markdown
            + current[proposal.end_offset :]
        )
        applied_hash = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        self._undo = (proposal.target_note, current, applied_hash)
        self.app._set_buffer_text(updated)
        self.app._flush_pending_save()
        self.pending_proposal = None
        self.apply_btn.set_visible(False)
        self.undo_btn.set_visible(True)
        self.app._show_toast(tr("AI proposal applied — you remain in control"))

    def _undo_ai_change(self, _button) -> None:
        if not self._undo:
            return
        note, content, applied_hash = self._undo
        if note != self.app.current_note:
            self.app._show_toast(tr("Open the changed note before undoing"))
            return
        current_hash = hashlib.sha256(self._buffer_text().encode("utf-8")).hexdigest()
        if current_hash != applied_hash:
            self.app._show_toast(tr("The note changed; AI undo was not applied"))
            return
        self.app._set_buffer_text(content)
        self.app._flush_pending_save()
        self._undo = None
        self.undo_btn.set_visible(False)
        self.app._show_toast(tr("AI change undone"))

    def _stop(self, _button) -> None:
        if self.cancel_token is not None:
            output = self._archive_active_generation()
            self._set_generating(False)
            self.prompt.get_buffer().set_text("")
            self._last_output = "" if output == tr("Stopped") else output
            self.copy_btn.set_visible(bool(self._last_output))
            self.summary_note_btn.set_visible(False)

    def _show_history(self, button) -> None:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        threads = self.history.load_all()
        for thread in threads[:30]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            item = Gtk.Button(label=thread.title)
            item.set_hexpand(True)
            item.connect("clicked", self._load_thread, thread, popover)
            row.append(item)
            delete = Gtk.Button(icon_name="user-trash-symbolic")
            delete.set_tooltip_text(tr("Delete chat"))
            delete.connect("clicked", self._delete_thread, thread, row)
            row.append(delete)
            box.append(row)
        if not threads:
            box.append(Gtk.Label(label=tr("No chat history")))
        clear = Gtk.Button(label=tr("Delete all history"))
        clear.add_css_class("destructive-action")
        clear.connect("clicked", self._delete_history, popover)
        box.append(clear)
        popover.set_child(box)
        popover.set_parent(button)
        popover.popup()

    def _load_thread(self, _button, thread: ChatThread, popover) -> None:
        self._archive_active_generation()
        self.thread = thread
        self._attach_public_notes()
        if thread.model in self._model_ids:
            self.model_dropdown.set_selected(self._model_ids.index(thread.model))
        self._clear_box(self.message_box)
        for message in thread.messages:
            self._add_message(message.role, message.content)
        if not thread.messages:
            self.message_box.append(self.empty_state)
        self._last_output = next(
            (
                message.content
                for message in reversed(thread.messages)
                if message.role == "assistant"
            ),
            "",
        )
        self.copy_btn.set_visible(bool(self._last_output))
        self.summary_note_btn.set_visible(False)
        self.apply_btn.set_visible(False)
        self._refresh_context_chips()
        popover.popdown()

    def _delete_history(self, _button, popover) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.app.win,
            heading=tr("Delete all chat history?"),
            body=tr("This removes local assistant threads from this vault."),
        )
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("delete", tr("Delete"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def responded(_dialog, response: str) -> None:
            if response == "delete":
                self.history.delete_all()
                popover.popdown()
                self.app._show_toast(tr("Chat history deleted"))

        dialog.connect("response", responded)
        dialog.present()

    def _delete_thread(self, _button, thread: ChatThread, row: Gtk.Box) -> None:
        self.history.delete(thread.id)
        parent = row.get_parent()
        if isinstance(parent, Gtk.Box):
            parent.remove(row)

    def dispose_panel(self) -> None:
        self._archive_active_generation()

    def purge_private_context(self) -> None:
        """Drop in-memory private-note context and output when notes lock."""
        if self.thread.ephemeral or any(a.encrypted for a in self.attachments):
            self.new_chat()
            self.app._show_toast(tr("Private assistant context cleared"))
