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
| `Ctrl + N` | New Note |
| `Ctrl + D` | Toggle Dashboard |
| `Ctrl + F` | Focus Search |
| `Escape` | Close Dashboard / Clear Search |
| `Ctrl + Q` | Quit |

## Task Deadlines
Add deadlines to checkboxes using: `[ ] Task name @YYYY-MM-DD` or `[ ] Task name @YYYY-MM-DD HH:MM`.

## Installation
Requires Python 3, GTK4, and Libadwaita.

```bash
# Arch Linux
sudo pacman -S python-gobject gtk4 libadwaita
git clone https://github.com/tanay1337/tokyo-notes.git
python3 main.py
```

## License
[MIT License](LICENSE)
