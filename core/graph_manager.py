import re
from pathlib import Path

class GraphManager:
    def __init__(self, notes_dir):
        self.notes_dir = Path(notes_dir)

    def get_graph_data(self):
        """Builds a map of Note -> [LinkedNotes]."""
        graph = {}
        note_files = list(self.notes_dir.glob("*.md"))
        
        # Initialize graph for all notes
        for note_file in note_files:
            graph[note_file.stem] = []
            
        # Populate links
        for note_file in note_files:
            content = note_file.read_text(encoding="utf-8")
            # Find [[Link]]
            links = re.findall(r'\[\[([^\]]+)\]\]', content)
            for link in links:
                if link in graph and link != note_file.stem:
                    graph[note_file.stem].append(link)
                    
        return graph
