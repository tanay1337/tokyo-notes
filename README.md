# Tokyo Notes

A minimal GTK4 Markdown note-taking app.

<img src="https://imglink.cc/cdn/qAXyRK27M4.png" width="450" /> <img src="https://imglink.cc/cdn/EOOk-V8NYw.png" width="450" />

## Features
- **Markdown Editor**: Live highlighting, code blocks, and lists.
- **Task Management**: Dashboard with Today, Week, and All views, featuring deadline tracking and jump-to-line navigation.
- **Automatic List Continuation**: Automatic list and task continuation when pressing `Enter`.
- **Thematic Animations**: "Sakura Celebration" particle effect when completing tasks in Dashboard.
- **Archive System**: Keep your workspace clean by archiving finished notes.
- **Knowledge Graph**: Visualize and navigate connections between your notes.
- **Deadlines & Pickers**: Type `@` for a deadline picker or `[[` for a note link picker.
- **Exporting**: Save notes as PDF or copy raw Markdown to the clipboard instantly.
- **Themes**: Multiple themes including Tokyo Night, Nord, and Cyberpunk 2077.
- **Image Support**: Inline display for local images and remote URLs.
- **Navigation**: Clickable links to external sites and internal note-to-note navigation.
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
| `Delete` | Delete selected note (with confirmation) |
| `Escape` | Close Dashboard / Clear Search / Return to Editor |
| `Ctrl/Cmd + Q` | Quit |

## Smart Syntax
- **Deadlines**: Type `@` to open a date/time picker for task deadlines.
- **Note Links**: Type `[[` to pick a note to link to.
- **Lists**: Press `Enter` on a list item to automatically continue the list. Press `Enter` twice to discontinue the lists. 
- **Task Markers**: Press `Enter` on a task `- [ ]` to create a new unchecked task.

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
