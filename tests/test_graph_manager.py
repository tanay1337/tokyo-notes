"""Tests for core/graph_manager.py — note-link graph building."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.graph_manager import GraphManager


def _make_mock_nm():
    """Return a MagicMock NotesManager with canned get_notes / get_metadata."""
    nm = MagicMock()

    def get_notes():
        return ["A", "B", "C", "Archived"]

    def get_metadata(name):
        links = {
            "A": ["B", "C"],
            "B": ["A"],
            "C": ["D"],  # D does not exist in get_notes → excluded from graph
            "Archived": ["A"],
        }
        return {"links": links.get(name, [])}

    nm.get_notes = get_notes
    nm.get_metadata = get_metadata
    return nm


class TestGraphManager:
    def test_get_graph_data_basic(self):
        gm = GraphManager(_make_mock_nm())
        graph = gm._get_graph_data()
        assert set(graph) == {"A", "B", "C", "Archived"}
        assert graph["A"] == ["B", "C"]
        assert graph["B"] == ["A"]
        assert graph["C"] == []  # D not in graph → filtered out

    def test_get_graph_data_excludes_archived(self):
        gm = GraphManager(_make_mock_nm())
        graph = gm._get_graph_data(archived_notes={"Archived"})
        assert "Archived" not in graph
        assert set(graph) == {"A", "B", "C"}

    def test_get_graph_data_no_self_links(self):
        nm = MagicMock()
        nm.get_notes = lambda: ["X"]
        nm.get_metadata = lambda n: {"links": ["X"]}
        gm = GraphManager(nm)
        graph = gm._get_graph_data()
        assert graph["X"] == []

    def test_get_graph_data_rich_includes_degrees(self):
        gm = GraphManager(_make_mock_nm())
        result = gm.get_graph_data_rich()
        assert "adjacency" in result
        assert "degrees" in result
        # A links to B, C (out=2), linked from B + Archived (in=2) → total 4
        assert result["degrees"]["A"] == 4
        # B links to A (out=1), linked from A (in=1) → total 2
        assert result["degrees"]["B"] == 2
