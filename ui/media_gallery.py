"""Visual gallery for images and PDFs stored with notes."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from core.media_index import MediaAsset, build_media_index
from core.translations import tr
from core.utils import confirm_destructive_dialog

logger = logging.getLogger(__name__)


class MediaGalleryView(Gtk.Box):
    """Non-destructive, filterable attachment gallery."""

    def __init__(
        self,
        *,
        notes_dir: str | Path,
        get_notes: Callable[[], list[str]],
        read_note: Callable[[str], str],
        readable_notes: Callable[[], set[str]],
        on_open_pdf: Callable[[str, str | None], None],
        on_open_diagram: Callable[[str], None] | None = None,
        on_go_to_note: Callable[[str, str], None] | None = None,
        on_return_to_note: Callable[[], None] | None = None,
        on_refresh: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._notes_dir = Path(notes_dir)
        self._get_notes = get_notes
        self._read_note = read_note
        self._readable_notes = readable_notes
        self._on_open_pdf = on_open_pdf
        self._on_open_diagram = on_open_diagram
        self._on_go_to_note = on_go_to_note
        self._on_return_to_note = on_return_to_note
        self._on_refresh = on_refresh
        self._assets: list[MediaAsset] = []
        self._generation = 0
        self._image_generation = 0
        self._image_return_to_note = False
        self._thumbs: dict[Path, GdkPixbuf.Pixbuf | None] = {}

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._build_gallery_page()
        self._build_image_page()
        self._stack.add_named(self._gallery_page, "gallery")
        self._stack.add_named(self._image_page, "image")
        self.append(self._stack)

    def _build_gallery_page(self) -> None:
        self._gallery_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(12)
        toolbar.set_margin_bottom(12)
        toolbar.set_margin_start(18)
        toolbar.set_margin_end(18)
        title = Gtk.Label(label=tr("Media"), xalign=0)
        title.add_css_class("view-title")
        title.set_hexpand(True)
        toolbar.append(title)

        self._type_dropdown = Gtk.DropDown.new_from_strings(
            [tr("All"), tr("Images"), tr("PDFs"), tr("Diagrams")]
        )
        self._type_dropdown.set_tooltip_text(tr("Filter by type"))
        self._type_dropdown.connect(
            "notify::selected", lambda *_: self._apply_filters()
        )
        toolbar.append(self._type_dropdown)
        self._reference_dropdown = Gtk.DropDown.new_from_strings(
            [tr("All"), tr("Referenced"), tr("Unreferenced")]
        )
        self._reference_dropdown.set_tooltip_text(tr("Filter by note reference"))
        self._reference_dropdown.connect(
            "notify::selected", lambda *_: self._apply_filters()
        )
        toolbar.append(self._reference_dropdown)
        refresh = Gtk.Button(
            child=Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        )
        refresh.set_tooltip_text(tr("Refresh media"))
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda *_: self.refresh())
        toolbar.append(refresh)
        self._gallery_page.append(toolbar)

        self._status = Gtk.Label(xalign=0)
        self._status.set_margin_start(18)
        self._status.set_margin_bottom(8)
        self._status.add_css_class("dim-label")
        self._gallery_page.append(self._status)

        self._flow = Gtk.FlowBox()
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_homogeneous(True)
        self._flow.set_row_spacing(16)
        self._flow.set_column_spacing(16)
        self._flow.set_margin_start(18)
        self._flow.set_margin_end(18)
        self._flow.set_margin_bottom(18)
        self._flow.set_min_children_per_line(3)
        self._flow.set_max_children_per_line(3)
        self._flow.set_vexpand(True)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._flow)
        self._gallery_page.append(scrolled)

    def _build_image_page(self) -> None:
        self._image_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(12)
        toolbar.set_margin_bottom(12)
        toolbar.set_margin_start(18)
        back = Gtk.Button(child=Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        self._image_back_button = back
        back.set_tooltip_text(tr("Back to media"))
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._on_image_back())
        toolbar.append(back)
        self._image_title = Gtk.Label(xalign=0)
        self._image_title.set_hexpand(True)
        self._image_title.add_css_class("view-title")
        toolbar.append(self._image_title)
        self._image_page.append(toolbar)
        self._image_picture = Gtk.Picture()
        self._image_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._image_picture.set_can_shrink(True)
        self._image_picture.set_vexpand(True)
        self._image_picture.set_hexpand(True)
        self._image_page.append(self._image_picture)

    def refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        self._status.set_label(tr("Loading media..."))
        self._clear_flow()

        def scan() -> None:
            assets = build_media_index(
                self._notes_dir,
                self._get_notes(),
                self._read_note,
                readable_notes=self._readable_notes(),
            )
            GLib.idle_add(self._finish_scan, generation, assets)

        threading.Thread(target=scan, daemon=True).start()

    def set_notes_dir(self, notes_dir: str | Path) -> None:
        self._notes_dir = Path(notes_dir)

    def _finish_scan(self, generation: int, assets: list[MediaAsset]) -> bool:
        if generation != self._generation:
            return False
        self._assets = assets
        self._thumbs.clear()
        self._apply_filters()
        return False

    def _clear_flow(self) -> None:
        while child := self._flow.get_first_child():
            self._flow.remove(child)

    def _apply_filters(self) -> None:
        if not hasattr(self, "_flow"):
            return
        self._clear_flow()
        type_index = self._type_dropdown.get_selected()
        ref_index = self._reference_dropdown.get_selected()
        assets = [
            asset
            for asset in self._assets
            if (
                type_index == 0
                or (type_index == 1 and asset.kind == "image")
                or (type_index == 2 and asset.kind == "pdf")
                or (type_index == 3 and asset.kind == "diagram")
            )
            and (
                ref_index == 0
                or (ref_index == 1 and asset.referenced)
                or (ref_index == 2 and not asset.referenced)
            )
        ]
        self._status.set_label(tr("{count} items").format(count=len(assets)))
        for asset in assets:
            self._flow.append(self._build_card(asset))
            self._load_thumbnail(asset)

    def _build_card(self, asset: MediaAsset) -> Gtk.Widget:
        button = Gtk.Button()
        button.add_css_class("media-card")
        button.set_size_request(152, 165)
        button.set_hexpand(True)
        button.set_halign(Gtk.Align.FILL)
        setattr(button, "_media_asset_path", asset.path)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_size_request(152, 165)
        body.set_hexpand(True)
        body.set_halign(Gtk.Align.FILL)
        preview_stack = Gtk.Stack()
        preview_stack.set_size_request(136, 120)
        preview_stack.set_hexpand(True)
        preview_stack.set_halign(Gtk.Align.FILL)
        fallback = Gtk.Image.new_from_icon_name(
            "application-pdf-symbolic"
            if asset.kind == "pdf"
            else "media-playlist-symbolic"
            if asset.kind == "diagram"
            else "image-x-generic-symbolic"
        )
        fallback.set_pixel_size(48)
        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_can_shrink(True)
        picture.set_size_request(136, 120)
        picture.set_hexpand(True)
        picture.set_vexpand(False)
        preview_stack.add_named(fallback, "fallback")
        preview_stack.add_named(picture, "picture")
        preview_stack.set_visible_child_name("fallback")
        setattr(button, "_media_preview_stack", preview_stack)
        setattr(button, "_media_picture", picture)
        preview_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_frame.set_size_request(136, 120)
        preview_frame.set_hexpand(True)
        preview_frame.set_halign(Gtk.Align.FILL)
        preview_frame.set_margin_start(8)
        preview_frame.set_margin_end(8)
        preview_frame.append(preview_stack)
        body.append(preview_frame)
        label = Gtk.Label(label=asset.title or asset.filename, xalign=0)
        label.set_ellipsize(3)
        label.set_hexpand(True)
        label.set_margin_start(8)
        label.set_margin_end(8)
        body.append(label)
        note_text = (
            ", ".join(asset.note_names) if asset.note_names else tr("Unreferenced")
        )
        notes = Gtk.Label(label=note_text, xalign=0)
        notes.add_css_class("dim-label")
        notes.set_ellipsize(3)
        notes.set_hexpand(True)
        notes.set_margin_start(8)
        notes.set_margin_end(8)
        body.append(notes)
        button.set_child(body)
        right_click = Gtk.GestureClick(button=3)
        right_click.connect(
            "pressed",
            lambda _gesture, _presses, x, y: self._show_context_menu(
                button, asset, x, y
            ),
        )
        button.add_controller(right_click)
        if asset.kind == "pdf":
            source = asset.source_refs[0][1] if asset.source_refs else str(asset.path)
            note = asset.source_refs[0][0] if asset.source_refs else None
            button.connect("clicked", lambda *_: self._on_open_pdf(source, note))
        elif asset.kind == "diagram" and self._on_open_diagram:
            button.connect(
                "clicked",
                lambda *_: self._on_open_diagram(asset.path.stem),
            )
        else:
            button.connect("clicked", lambda *_: self._open_gallery_image(asset))
        return button

    def _show_context_menu(
        self, button: Gtk.Button, asset: MediaAsset, x: float, y: float
    ) -> None:
        popover = Gtk.Popover()
        popover.set_parent(button)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.set_margin_top(4)
        menu.set_margin_bottom(4)
        menu.set_margin_start(4)
        menu.set_margin_end(4)

        copy_reference = Gtk.Button(label=tr("Copy reference"))
        copy_reference.connect(
            "clicked",
            lambda *_: (
                popover.popdown(),
                self._copy_reference(asset),
            ),
        )
        menu.append(copy_reference)

        go_to_note = Gtk.Button(label=tr("Go to Note"))
        go_to_note.set_sensitive(bool(asset.note_names and self._on_go_to_note))
        if asset.note_names and self._on_go_to_note:
            source_ref = (
                asset.source_refs[0][1] if asset.source_refs else asset.filename
            )
            go_to_note.connect(
                "clicked",
                lambda *_: (
                    popover.popdown(),
                    self._on_go_to_note(asset.note_names[0], source_ref),
                ),
            )
        menu.append(go_to_note)

        delete = Gtk.Button(label=tr("Delete"))
        delete.add_css_class("destructive-action")
        delete.connect(
            "clicked", lambda *_: (popover.popdown(), self._confirm_delete(asset))
        )
        menu.append(delete)
        popover.set_child(menu)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _copy_reference(self, asset: MediaAsset) -> None:
        """Copy a ready-to-paste Markdown embed for an asset."""
        if asset.kind == "diagram":
            reference = (
                asset.source_refs[0][1] if asset.source_refs else asset.path.stem
            )
            markdown = f"![diagram]({reference})"
        else:
            if asset.source_refs:
                reference = asset.source_refs[0][1]
                title = asset.title or asset.filename
            else:
                try:
                    reference = asset.path.relative_to(self._notes_dir).as_posix()
                except ValueError:
                    reference = asset.path.name
                title = asset.filename
            markdown = f"![{title}]({reference})"

        root = self.get_root()
        if root is not None:
            clipboard = root.get_clipboard()
            clipboard.set_content(Gdk.ContentProvider.new_for_value(markdown))

    def _confirm_delete(self, asset: MediaAsset) -> None:
        root = self.get_root()
        if root is None:
            return
        if asset.note_names:
            body = tr(
                "This asset is referenced by {count} note(s). Deleting it will "
                "break those references. This action cannot be undone."
            ).format(count=len(asset.note_names))
        else:
            body = tr("This action cannot be undone.")
        dialog = confirm_destructive_dialog(
            transient_for=root,
            heading=tr("Delete {name}?").format(name=asset.filename),
            body=body,
        )
        dialog.connect("response", self._on_delete_response, asset)
        dialog.present()

    def _on_delete_response(self, _dialog, response: str, asset: MediaAsset) -> None:
        if response != "delete":
            return
        try:
            asset.path.unlink()
        except OSError as exc:
            logger.warning("Could not delete media asset %s: %s", asset.path, exc)
            return
        if self._on_refresh:
            self._on_refresh()
        else:
            self.refresh()

    def _load_thumbnail(self, asset: MediaAsset) -> None:
        generation = self._generation

        def load() -> None:
            pixbuf = self._render_thumbnail(asset)
            self._thumbs[asset.path] = pixbuf
            GLib.idle_add(self._set_thumbnail, generation, asset.path, pixbuf)

        threading.Thread(target=load, daemon=True).start()

    def _render_thumbnail(self, asset: MediaAsset) -> GdkPixbuf.Pixbuf | None:
        try:
            if asset.kind == "image":
                # Keep a 2x display-resolution source and let Gtk perform
                # the final fit once, avoiding soft double-resampling.
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(asset.path), 272, 240, True
                )
            if asset.kind == "diagram":
                from core.diagram import Diagram
                from ui.diagram_view import render_diagram_preview

                data = __import__("json").loads(asset.path.read_text(encoding="utf-8"))
                return render_diagram_preview(Diagram.from_dict(data), 840, 560)
            pdftoppm = shutil.which("pdftoppm")
            if not pdftoppm:
                return None
            result = subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-scale-to",
                    "640",
                    str(asset.path),
                    "-",
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0 or not result.stdout:
                return None
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(result.stdout)
            loader.close()
            return self._square_thumbnail(loader.get_pixbuf())
        except Exception as exc:
            logger.debug("Could not render media thumbnail %s: %s", asset.path, exc)
            return None

    @staticmethod
    def _square_thumbnail(pixbuf):
        if pixbuf is None:
            return None
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            return None
        scale = min(136 / width, 120 / height)
        target_w = max(1, int(width * scale))
        target_h = max(1, int(height * scale))
        return pixbuf.scale_simple(target_w, target_h, GdkPixbuf.InterpType.HYPER)

    def _set_thumbnail(self, generation: int, path: Path, pixbuf) -> bool:
        if generation != self._generation:
            return False
        child = self._flow.get_first_child()
        while child:
            button = child.get_child()
            if (
                button is not None
                and getattr(button, "_media_asset_path", None) == path
            ):
                picture = getattr(button, "_media_picture", None)
                stack = getattr(button, "_media_preview_stack", None)
                if picture is not None and stack is not None and pixbuf is not None:
                    picture.set_pixbuf(pixbuf)
                    stack.set_visible_child_name("picture")
                break
            child = child.get_next_sibling()
        return False

    def _show_image(self, asset: MediaAsset) -> None:
        self._image_title.set_label(asset.filename)
        self._stack.set_visible_child_name("image")
        self._image_generation += 1
        generation = self._image_generation
        self._image_picture.set_pixbuf(None)

        def load() -> None:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(asset.path))
            except Exception as exc:
                logger.debug("Could not open image %s: %s", asset.path, exc)
                pixbuf = None
            GLib.idle_add(self._finish_image, generation, pixbuf)

        threading.Thread(target=load, daemon=True).start()

    def _open_gallery_image(self, asset: MediaAsset) -> None:
        self._image_return_to_note = False
        self._image_back_button.set_tooltip_text(tr("Back to media"))
        self._show_image(asset)

    def open_image(self, path_or_url: str, *, return_to_note: bool = False) -> None:
        """Show an image directly, including when opened from an editor embed."""
        clean_path = path_or_url.split("#", 1)[0].split("?", 1)[0]
        if clean_path.startswith(("http://", "https://")):
            path = Path(clean_path)
        else:
            path = Path(clean_path).expanduser()
            if not path.is_absolute():
                path = (self._notes_dir / path).resolve()
        asset = next((item for item in self._assets if item.path == path), None)
        if asset is None:
            asset = MediaAsset(
                path=path,
                kind="image",
                filename=path.name,
                modified=0.0,
            )
        self._image_return_to_note = return_to_note
        self._image_back_button.set_tooltip_text(
            tr("Back to note") if return_to_note else tr("Back to media")
        )
        self._show_image(asset)

    def _on_image_back(self) -> None:
        if self._image_return_to_note and self._on_return_to_note:
            self._image_return_to_note = False
            self._on_return_to_note()
        else:
            self._stack.set_visible_child_name("gallery")

    def _finish_image(self, generation: int, pixbuf) -> bool:
        if generation == self._image_generation and pixbuf is not None:
            self._image_picture.set_pixbuf(pixbuf)
        return False
