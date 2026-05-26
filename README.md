# Tokyo Notes

A minimal GTK4 Markdown note-taking app.

<img src="https://imglink.cc/cdn/P8DiOqf543.png" width="500" />

## Features
- **Task Management**: Dashboard with Today, Week, and All views, featuring deadline tracking and jump-to-line navigation.
- **Private Notes**: AES-256-GCM encrypted notes with per-file salt, Argon2id key derivation, and inactivity auto-lock.
- **Thematic Animations**: "Sakura Celebration" particle effect when completing tasks in Dashboard.
- **Archive System**: Keep your workspace clean by archiving finished notes.
- **Knowledge Graph**: Visualize and navigate connections between your notes.
- **Themes**: Multiple themes including Tokyo Night, Nord, and Cyberpunk 2077.
- **Full-Text Search**: Find notes by title or keyword content.
- **Templates**: Create notes from reusable templates with auto-filled variables like `{{today}}`, `{{now}}`, `{{time}}`, and `{{weekday}}`.
- **Version History**: Automatic save-points, manual snapshots, and one-click restore from any previous version via Git.
- **Flashcards**: Extract Q/A cards from flashcard codeblocks and flip through them to review. See format below.

## Installation
Requires Python 3.12+, PyGObject, GTK4, Libadwaita, and Git.

```bash
# Arch Linux
yay -S tokyo-notes-git

# macOS (using Homebrew to install dependencies)
brew install gtk4 libadwaita pygobject3 git
pip3 install --break-system-packages gitpython
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
| `Ctrl/Cmd + Shift + F` | Flashcards |
| `Ctrl/Cmd + F` | Focus Search |
| `Ctrl/Cmd + G` | Knowledge Graph |
| `Ctrl/Cmd + L` | Lock Private Notes |
| `Ctrl/Cmd + T` | Quick Add Task |
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

## Flashcards
Here's the format to create flashcards in your notes.

~~~
```flashcard
Question
---
Answer
```
~~~

## License
[MIT License](LICENSE)
