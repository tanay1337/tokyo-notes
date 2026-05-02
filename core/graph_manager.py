import re
from pathlib import Path

class GraphManager:
    def __init__(self, notes_manager):
        self.notes_manager = notes_manager

    def get_graph_data(self, archived_notes=None):
        """Builds a map of Note -> [LinkedNotes] using cached metadata."""
        graph = {}
        note_names = self.notes_manager.get_notes(archived_notes=archived_notes)
        
        # Initialize graph for all notes
        for name in note_names:
            graph[name] = []
            
        # Populate links using metadata cache
        for name in note_names:
            metadata = self.notes_manager.get_metadata(name)
            links = metadata.get('links', [])
            for link in links:
                # Only add link if destination is also not archived
                if link in graph and link != name:
                    graph[name].append(link)
                    
        return graph
