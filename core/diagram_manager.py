"""Diagram storage — CRUD for .diagrams/{id}.json files."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from core.diagram import Diagram

logger = logging.getLogger(__name__)


class DiagramManager:
    """Manages saving, loading, and listing diagram files."""

    _DIAGRAMS_DIR = ".diagrams"

    def __init__(self, notes_dir: str | Path) -> None:
        self.notes_dir = Path(notes_dir)
        self._diagrams_dir: Path = self.notes_dir / self._DIAGRAMS_DIR

    def _ensure_dir(self) -> Path:
        self._diagrams_dir.mkdir(parents=True, exist_ok=True)
        return self._diagrams_dir

    def _path_for(self, diagram_id: str) -> Path:
        return self._ensure_dir() / f"{diagram_id}.json"

    def save(self, diagram: Diagram) -> None:
        """Write a diagram to disk as JSON."""
        path = self._path_for(diagram.id)
        data = diagram.as_dict()
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.error("Failed to save diagram '%s': %s", diagram.id, e)
            raise

    def load(self, diagram_id: str) -> Diagram | None:
        """Read a diagram from disk by ID. Returns None if not found."""
        path = self._path_for(diagram_id)
        if not path.exists():
            logger.warning("Diagram '%s' not found at %s", diagram_id, path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Diagram.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error("Failed to load diagram '%s': %s", diagram_id, e)
            return None

    def delete(self, diagram_id: str) -> bool:
        """Delete a diagram file. Returns True on success."""
        path = self._path_for(diagram_id)
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as e:
            logger.error("Failed to delete diagram '%s': %s", diagram_id, e)
            return False

    def list_ids(self) -> list[str]:
        """Return all diagram IDs found on disk."""
        if not self._diagrams_dir.exists():
            return []
        return sorted(p.stem for p in self._diagrams_dir.glob("*.json"))

    def list_titles(self) -> list[tuple[str, str]]:
        """Return (id, title) pairs for all diagrams."""
        result: list[tuple[str, str]] = []
        for did in self.list_ids():
            diagram = self.load(did)
            if diagram:
                result.append((did, diagram.title))
        return result

    @staticmethod
    def generate_id() -> str:
        return uuid.uuid4().hex[:12]
