"""Conditionally mock GTK/GI for environments without the runtime installed."""

from __future__ import annotations

import sys

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    HAS_GTK = True
except (ImportError, ValueError, AttributeError):
    HAS_GTK = False

if not HAS_GTK:
    from unittest.mock import MagicMock

    gi = MagicMock()
    gi.require_version = MagicMock()
    sys.modules["gi"] = gi

    repo = MagicMock()
    repo.Gtk = MagicMock()
    repo.GLib = MagicMock()
    repo.GLib.timeout_add.side_effect = lambda d, c, *a: 1
    repo.GLib.source_remove.return_value = True
    repo.Gio = MagicMock()
    repo.Gdk = MagicMock()
    repo.Pango = MagicMock()
    repo.Adw = MagicMock()
    repo.cairo = MagicMock()
    sys.modules["gi.repository"] = repo
