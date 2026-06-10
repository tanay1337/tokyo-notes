"""PyInstaller hook for pyspellchecker.

Collects bundled dictionary data files (.json.gz) so spell checking
works in the packaged app.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("spellchecker", includes=["**/*.json.gz"])
