"""PyInstaller hook for Tokyo Notes' local markdown package.

PyInstaller's contributed hook named ``hook-markdown.py`` targets the third-party
PyPI package ``Markdown``. Tokyo Notes has an internal package named
``markdown``, so the contributed hook incorrectly tries to copy distribution
metadata that does not exist in this app.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("markdown")
