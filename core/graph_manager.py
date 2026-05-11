"""Graph data construction for the knowledge graph view."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.storage import NotesManager


class GraphManager:
    """Builds a note-link adjacency map from cached NotesManager metadata."""

    def __init__(self, notes_manager: "NotesManager") -> None:
        self.notes_manager = notes_manager

    def get_graph_data(self, archived_notes: set[str] | None = None) -> dict[str, list[str]]:
        """Return a {note: [linked_notes]} map, excluding archived notes."""
        all_notes = self.notes_manager.get_notes()
        visible = {n for n in all_notes if not (archived_notes and n in archived_notes)}

        graph: dict[str, list[str]] = {name: [] for name in visible}

        for name in visible:
            metadata: dict[str, Any] = self.notes_manager.get_metadata(name)
            for link in metadata.get("links", []):
                if link in graph and link != name:
                    graph[name].append(link)

        return graph
