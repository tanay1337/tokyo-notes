"""Runtime environment fixes for the frozen macOS GTK app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()

share_dirs = [
    bundle_root / "share",
    bundle_root / "_internal" / "share",
]
typelib_dirs = [
    bundle_root / "lib" / "girepository-1.0",
    bundle_root / "_internal" / "lib" / "girepository-1.0",
]

existing_share = [str(path) for path in share_dirs if path.exists()]
existing_typelib = [str(path) for path in typelib_dirs if path.exists()]

if existing_share:
    previous = os.environ.get("XDG_DATA_DIRS")
    os.environ["XDG_DATA_DIRS"] = ":".join(
        existing_share + ([previous] if previous else [])
    )
    os.environ.setdefault("GTK_DATA_PREFIX", str(bundle_root))

if existing_typelib:
    previous = os.environ.get("GI_TYPELIB_PATH")
    os.environ["GI_TYPELIB_PATH"] = ":".join(
        existing_typelib + ([previous] if previous else [])
    )
