# Tokyo Notes

A minimal GTK4 Markdown note-taking app.

<img src="https://imglink.cc/cdn/P8DiOqf543.png" width="500" />

## Features
- **Task Management**: Dashboard with Today, Week, and All views, featuring deadline tracking and jump-to-line navigation.
- **Private Notes**: AES-256-GCM encrypted notes with per-file salt, inactivity auto-lock, and brute-force protection.
- **Thematic Animations**: "Sakura Celebration" particle effect when completing tasks in Dashboard.
- **Archive System**: Keep your workspace clean by archiving finished notes.
- **Knowledge Graph**: Visualize and navigate connections between your notes.
- **Themes**: Multiple themes including Tokyo Night, Nord, and Cyberpunk 2077.
- **Full-Text Search**: Find notes by title or keyword content.
- **Templates**: Create notes from reusable templates with auto-filled variables like `{{today}}`, `{{now}}`, `{{time}}`, and `{{weekday}}`.

## Installation
Requires Python 3, PyGObject, GTK4, Libadwaita, and Libadwaita Icons.

```bash
# Arch Linux
yay -S tokyo-notes-git

# macOS (using Homebrew to install dependencies)
brew install gtk4 libadwaita pygobject3 adwaita-icon-theme
git clone https://github.com/tanay1337/tokyo-notes.git
cd tokyo-notes
python3 main.py

# Others (after installing dependencies)
git clone https://github.com/tanay1337/tokyo-notes.git
cd tokyo-notes
python3 main.py
```

## Keyboard Shortcuts
| Shortcut | Action |
| :--- | :--- |
| `Ctrl/Cmd + N` | New Note |
| `Ctrl/Cmd + Shift + N` | New Note from Template |
| `Ctrl/Cmd + D` | Toggle Dashboard |
| `Ctrl/Cmd + F` | Focus Search |
| `Ctrl/Cmd + G` | Knowledge Graph |
| `Ctrl/Cmd + L` | Lock Private Notes |
| `Ctrl/Cmd + Shift + T` | Insert Timestamp |
| `Ctrl/Cmd + Shift + Z` | Zen Mode |
| `Delete` | Delete selected note (with confirmation) |
| `Escape` | Close Dashboard / Clear Search / Return to Editor |
| `Ctrl/Cmd + Q` | Quit |

## Smart Syntax
- **Deadlines**: Type `@` to open a date/time picker for task deadlines.
- **Note Links**: Type `[[` to pick a note to link to.
- **Lists**: Press `Enter` on a list item to automatically continue the list. Press `Enter` twice to discontinue the lists. 
- **Task Markers**: Press `Enter` on a task `- [ ]` to create a new unchecked task.

## License
[MIT License](LICENSE)
