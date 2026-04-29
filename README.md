# Tokyo Notes

A minimal GTK4 Markdown note-taking app.

<img src="https://imglink.cc/cdn/qAXyRK27M4.png" width="450" /> <img src="https://imglink.cc/cdn/EOOk-V8NYw.png" width="450" />

## Features
- **Markdown Editor**: Live highlighting, code blocks, and task lists with checkboxes.
- **Image Support**: Inline display for local images and remote URLs (HTTPS).
- **Navigation**: Clickable links to external sites and internal note-to-note navigation.
- **Task Management**: Dashboard with Today, Week, and All views, featuring deadline tracking and precision jump-to-line navigation.
- **Full-Text Search**: Find notes by title or keyword content.
- **Status Bar**: Real-time word count, character count, and reading time.
- **Persistent State**: Remembers UI layout (sidebar/toolbar visibility) and settings.

## Keyboard Shortcuts
| Shortcut | Action |
| :--- | :--- |
| `Ctrl/Cmd + N` | New Note |
| `Ctrl/Cmd + D` | Toggle Dashboard |
| `Ctrl/Cmd + F` | Focus Search |
| `Ctrl/Cmd + G` | Knowledge Graph |
| `Escape` | Close Dashboard / Clear Search |
| `Ctrl/Cmd + Q` | Quit |

## Task Deadlines
Add deadlines to checkboxes using: `[ ] Task name @YYYY-MM-DD` or `[ ] Task name @YYYY-MM-DD HH:MM`.

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

## License
[MIT License](LICENSE)
