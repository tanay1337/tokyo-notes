"""Template manager — handles built-in and user templates."""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import TokyoNotes

logger = logging.getLogger(__name__)

TEMPLATES_DIR_NAME = ".templates"
_VALID_TEMPLATE_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_FRONT_MATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n(?:---|\.\.\.)\s*\n",
    re.DOTALL,
)


class TemplateManager:
    """Manages template provisioning, CRUD, and variable substitution."""

    def __init__(self, app: TokyoNotes) -> None:
        self.app = app

    @staticmethod
    def _parse_front_matter(content: str) -> tuple[dict[str, str], str]:
        """Extract YAML front matter from template content.

        Returns (metadata, body).  If no front matter is present the
        metadata dict is empty and *body* is the original *content*.
        """
        m = _FRONT_MATTER_RE.match(content)
        if not m:
            return {}, content
        raw = m.group(1)
        metadata: dict[str, str] = {}
        for line in raw.strip().split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                metadata[key.strip()] = val.strip()
        return metadata, content[m.end() :]

    @property
    def templates_dir(self) -> Path:
        """Path to the .templates/ directory inside the notes folder."""
        return Path(self.app.notes_folder) / TEMPLATES_DIR_NAME

    def _ensure_templates_dir(self) -> None:
        """Create .templates/ if it doesn't exist, and provision built-ins."""
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self._provision_builtins()

    def _provision_builtins(self) -> None:
        """Write built-in templates that don't already exist."""
        from core.templates_builtin import BUILTIN_TEMPLATES

        for slug, template in BUILTIN_TEMPLATES.items():
            path = self.templates_dir / f"{slug}.md"
            if not path.exists():
                path.write_text(template["content"], encoding="utf-8")
                logger.info("Provisioned built-in template: %s", slug)

    def get_all_templates(self) -> list[dict[str, Any]]:
        """Return all templates (built-in + user) as a list of dicts.

        Each dict has: slug, name, content, is_builtin, description,
        and folder (default target folder from front matter, or '').
        """
        from core.templates_builtin import BUILTIN_TEMPLATES

        self._ensure_templates_dir()

        templates: list[dict[str, Any]] = []
        builtins_by_file = {
            f"{slug}.md": info for slug, info in BUILTIN_TEMPLATES.items()
        }

        for path in sorted(self.templates_dir.glob("*.md")):
            slug = path.stem
            raw = path.read_text(encoding="utf-8")
            metadata, content = self._parse_front_matter(raw)
            builtin_info = builtins_by_file.get(path.name)
            templates.append(
                {
                    "slug": slug,
                    "name": builtin_info["name"] if builtin_info else slug,
                    "content": content,
                    "is_builtin": builtin_info is not None,
                    "description": builtin_info["description"] if builtin_info else "",
                    "folder": metadata.get("folder", ""),
                }
            )

        return templates

    def get_template_content(self, slug: str) -> str | None:
        """Return the body of a template (front matter stripped) by slug, or None."""
        self._ensure_templates_dir()
        self._validate_slug(slug)
        path = self.templates_dir / f"{slug}.md"
        if path.exists():
            _metadata, body = self._parse_front_matter(path.read_text(encoding="utf-8"))
            return body
        return None

    def get_template_folder(self, slug: str) -> str:
        """Return the default folder from a template's front matter, or ''."""
        self._ensure_templates_dir()
        self._validate_slug(slug)
        path = self.templates_dir / f"{slug}.md"
        if path.exists():
            metadata, _body = self._parse_front_matter(path.read_text(encoding="utf-8"))
            return metadata.get("folder", "")
        return ""

    def save_as_template(self, name: str, content: str) -> str:
        """Save *content* as a new user template. Returns the slug."""
        self._ensure_templates_dir()
        slug = self._make_slug(name)
        slug = self._reserve_slug(slug)
        path = self.templates_dir / f"{slug}.md"
        path.write_text(content, encoding="utf-8")
        logger.info("Saved user template: %s", slug)
        return slug

    def delete_template(self, slug: str) -> bool:
        """Delete a template by slug. Returns True if deleted."""
        self._ensure_templates_dir()
        self._validate_slug(slug)
        path = self.templates_dir / f"{slug}.md"
        if path.exists():
            path.unlink()
            logger.info("Deleted template: %s", slug)
            return True
        return False

    def update_template(self, slug: str, content: str) -> bool:
        """Update a template's content. Returns True if updated."""
        self._ensure_templates_dir()
        self._validate_slug(slug)
        path = self.templates_dir / f"{slug}.md"
        if path.exists():
            path.write_text(content, encoding="utf-8")
            return True
        return False

    @staticmethod
    def substitute_variables(content: str) -> str:
        """Replace {{variable}} placeholders with current values.

        Supported variables: today, now, time, weekday.
        """
        now = datetime.datetime.now()
        variables = {
            "today": now.strftime("%Y-%m-%d"),
            "now": now.strftime("%Y-%m-%d %H:%M"),
            "time": now.strftime("%H:%M"),
            "weekday": now.strftime("%A"),
        }
        result = content
        for var, value in variables.items():
            result = result.replace(f"{{{{{var}}}}}", value)
        return result

    @staticmethod
    def _make_slug(name: str) -> str:
        """Convert a template name to a filesystem-safe slug."""
        slug = name.lower().strip()
        slug = slug.replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c in "-_")
        if not slug:
            slug = "untitled"
        return slug

    @staticmethod
    def _validate_slug(slug: str) -> None:
        """Reject slugs that could escape the templates directory."""
        if not _VALID_TEMPLATE_SLUG_RE.fullmatch(slug):
            raise ValueError(f"Invalid template slug: {slug!r}")

    def _reserve_slug(self, slug: str) -> str:
        """Return *slug* or a numbered variant that does not already exist."""
        self._validate_slug(slug)
        candidate = slug
        counter = 1
        while (self.templates_dir / f"{candidate}.md").exists():
            candidate = f"{slug}-{counter}"
            counter += 1
        return candidate
