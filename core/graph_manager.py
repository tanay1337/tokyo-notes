"""Graph data construction for the knowledge graph view."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.storage import NotesManager


class GraphManager:
    """Builds a note-link adjacency map from cached NotesManager metadata."""

    def __init__(self, notes_manager: NotesManager) -> None:
        self.notes_manager = notes_manager

    def _get_graph_data(
        self, archived_notes: set[str] | None = None
    ) -> dict[str, list[str]]:
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

    def get_graph_data_rich(self, archived_notes: set[str] | None = None) -> dict:
        """Return graph data with degree info for node sizing."""
        adjacency = self._get_graph_data(archived_notes)

        in_degrees: dict[str, int] = {node: 0 for node in adjacency}
        for node, links in adjacency.items():
            for target in links:
                if target in in_degrees:
                    in_degrees[target] += 1

        degrees: dict[str, int] = {
            node: in_degrees[node] + len(links) for node, links in adjacency.items()
        }

        return {
            "adjacency": adjacency,
            "degrees": degrees,
        }
