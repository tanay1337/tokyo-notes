# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


DICTATION = os.environ.get("TOKYO_NOTES_DICTATION") == "1"
ROOT = Path(SPECPATH).parents[1]
ICON = ROOT / "packaging" / "macos" / "TokyoNotes.icns"


datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "themes"), "themes"),
    (str(ROOT / "style.css"), "."),
    (str(ROOT / "translations"), "translations"),
    (str(ROOT / "core" / "templates"), "core/templates"),
]

hiddenimports = [
    "gi.repository.Adw",
    "gi.repository.Gdk",
    "gi.repository.Gio",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "gi.repository.Gtk",
]
hiddenimports += collect_submodules("core")
hiddenimports += collect_submodules("markdown")
hiddenimports += collect_submodules("ui")

# ── speech deps, only collected for the dictation variant ─────────────

if DICTATION:
    hiddenimports += [
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "huggingface_hub",
        "numpy",
        "tqdm",
    ]
    speech_binaries = collect_dynamic_libs("sounddevice")
else:
    speech_binaries = []


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[] + speech_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "macos" / "hooks")],
    hooksconfig={
        "gi": {
            "icons": ["Adwaita", "hicolor"],
            "themes": ["Default", "Adwaita"],
            "languages": ["en"],
            "module-versions": {
                "Gtk": "4.0",
                "Gdk": "4.0",
            },
        },
    },
    runtime_hooks=[str(ROOT / "packaging" / "macos" / "runtime-hook.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tokyo Notes",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Tokyo Notes",
)

info_plist = {
    "CFBundleName": "Tokyo Notes",
    "CFBundleDisplayName": "Tokyo Notes",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundleVersion": "0.1.0",
    "LSMinimumSystemVersion": "13.0",
    "NSHighResolutionCapable": True,
    "NSRequiresAquaSystemAppearance": False,
}

if DICTATION:
    info_plist["NSMicrophoneUsageDescription"] = (
        "Tokyo Notes uses the microphone for local speech-to-text dictation. "
        "Audio is processed on your device and never sent anywhere."
    )

app = BUNDLE(
    coll,
    name="Tokyo Notes.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="app.tokyo-notes.TokyoNotes",
    info_plist=info_plist,
)
