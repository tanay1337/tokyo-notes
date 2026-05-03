"""Graph data management for linking notes."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.storage import NotesManager

class GraphManager:
    def __init__(self, notes_manager: NotesManager) -> None:
        self.notes_manager: NotesManager = notes_manager

    def get_graph_data(self, archived_notes: set[str] | None = None) -> dict[str, list[str]]:
        """Builds a map of Note -> [LinkedNotes] using cached metadata."""
        graph: dict[str, list[str]] = {}
        note_names: list[str] = self.notes_manager.get_notes(archived_notes=archived_notes)
        
        # Initialize graph for all notes
        for name in note_names:
            graph[name] = []
            
        # Populate links using metadata cache
        for name in note_names:
            metadata: dict[str, Any] = self.notes_manager.get_metadata(name)
            links: list[str] = metadata.get('links', [])
            for link in links:
                # Only add link if destination is also not archived
                if link in graph and link != name:
                    graph[name].append(link)
                    
        return graph
