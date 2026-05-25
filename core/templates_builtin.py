"""Built-in template definitions.

These are provisioned into the user's .templates/ directory on first access.
Each template is a dict with 'name', 'content', and optional 'description'.
Content supports {{variable}} substitution at insertion time.
"""

from __future__ import annotations

BUILTIN_TEMPLATES: dict[str, dict[str, str]] = {
    "daily-journal": {
        "name": "Daily Journal",
        "description": "Date header, gratitude, tasks, and reflections",
        "content": (
            "# {{weekday}}, {{today}}\n"
            "\n"
            "## Gratitude\n"
            "- \n"
            "\n"
            "## Tasks\n"
            "- [ ] \n"
            "\n"
            "## Notes\n"
            "\n"
            "## Reflections\n"
            "\n"
        ),
    },
}
