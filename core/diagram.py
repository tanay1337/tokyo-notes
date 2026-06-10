"""Diagram data model — nodes, edges, and tree layout algorithm."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

_SHAPE_DEFAULTS = {"pill", "rectangle", "circle", "diamond"}
_EDGE_TYPES = {"solid", "dashed", "dotted", "double", "arrow", "bidirect"}


@dataclass
class DiagramNode:
    id: str
    text: str
    x: float
    y: float
    w: float = 100.0
    h: float = 40.0
    color: str = "#4a90d9"
    shape: str = "pill"

    def __post_init__(self) -> None:
        if self.shape not in _SHAPE_DEFAULTS:
            self.shape = "pill"

    @staticmethod
    def new(text: str = "New Node", x: float = 0.0, y: float = 0.0) -> DiagramNode:
        return DiagramNode(
            id=uuid.uuid4().hex[:12],
            text=text,
            x=x,
            y=y,
        )


@dataclass
class DiagramEdge:
    id: str
    from_id: str
    to_id: str
    edge_type: str = "arrow"
    label: str = ""

    def __post_init__(self) -> None:
        if self.edge_type not in _EDGE_TYPES:
            self.edge_type = "arrow"

    @staticmethod
    def new(from_id: str, to_id: str) -> DiagramEdge:
        return DiagramEdge(
            id=uuid.uuid4().hex[:12],
            from_id=from_id,
            to_id=to_id,
        )


@dataclass
class Diagram:
    id: str
    title: str
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)

    @staticmethod
    def new(title: str = "Untitled Diagram") -> Diagram:
        return Diagram(
            id=uuid.uuid4().hex[:12],
            title=title,
            nodes=[DiagramNode.new("Root", 0.0, 0.0)],
            edges=[],
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "nodes": [
                {
                    "id": n.id,
                    "text": n.text,
                    "x": n.x,
                    "y": n.y,
                    "w": n.w,
                    "h": n.h,
                    "color": n.color,
                    "shape": n.shape,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "from_id": e.from_id,
                    "to_id": e.to_id,
                    "edge_type": e.edge_type,
                    "label": e.label,
                }
                for e in self.edges
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> Diagram:
        return Diagram(
            id=data["id"],
            title=data.get("title", "Untitled Diagram"),
            nodes=[DiagramNode(**n) for n in data.get("nodes", [])],
            edges=[DiagramEdge(**e) for e in data.get("edges", [])],
        )

    def copy(self) -> Diagram:
        """Return a deep-ish copy via JSON roundtrip."""
        return Diagram.from_dict(self.as_dict())

    def find_node(self, node_id: str) -> Optional[DiagramNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def remove_node(self, node_id: str) -> None:
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.edges = [
            e for e in self.edges if e.from_id != node_id and e.to_id != node_id
        ]

    def children_of(self, node_id: str) -> list[DiagramNode]:
        child_ids = {e.to_id for e in self.edges if e.from_id == node_id}
        return [n for n in self.nodes if n.id in child_ids]

    def parent_of(self, node_id: str) -> Optional[DiagramNode]:
        for e in self.edges:
            if e.to_id == node_id:
                return self.find_node(e.from_id)
        return None

    def tree_layout(self, spacing_x: float = 40.0, spacing_y: float = 100.0) -> None:
        """Arrange nodes in a top-down tree layout.

        Uses BFS level assignment, centering each parent over its children.
        Nodes with no parent are treated as roots and placed on level 0.
        """
        if not self.nodes:
            return

        roots = [n for n in self.nodes if self.parent_of(n.id) is None]
        if not roots:
            return

        level_nodes: dict[int, list[DiagramNode]] = {}
        visited: set[str] = set()

        def bfs(root: DiagramNode) -> None:
            queue: list[tuple[DiagramNode, int]] = [(root, 0)]
            while queue:
                node, level = queue.pop(0)
                if node.id in visited:
                    continue
                visited.add(node.id)
                level_nodes.setdefault(level, []).append(node)
                for child in self.children_of(node.id):
                    if child.id not in visited:
                        queue.append((child, level + 1))

        for root in roots:
            bfs(root)

        # Handle disconnected nodes
        for node in self.nodes:
            if node.id not in visited:
                root = DiagramNode.new("_orphan")
                visited.add(node.id)
                level_nodes.setdefault(0, []).append(node)

        if not level_nodes:
            return

        max_level = max(level_nodes.keys())
        root_width_by_level: dict[int, float] = {}

        for level in range(max_level, -1, -1):
            nodes_at_level = level_nodes.get(level, [])
            if not nodes_at_level:
                continue

            width = (len(nodes_at_level) - 1) * spacing_x
            for n in nodes_at_level:
                children = self.children_of(n.id)
                if children:
                    cx = sum(c.x for c in children) / len(children)
                    ch = sum(self._node_width(c) for c in children)
                    cw = (len(children) - 1) * spacing_x
                    n.x = cx
                    total_child_width = cw + ch
                    if total_child_width > self._node_width(n):
                        n.x = cx
                    root_width_by_level[n.id] = max(
                        self._node_width(n), total_child_width
                    )
                else:
                    root_width_by_level[n.id] = self._node_width(n)

            start_x = -(width / 2)
            for i, n in enumerate(nodes_at_level):
                n.x = start_x + i * spacing_x
                n.y = float(level * spacing_y)

        if len(roots) == 1:
            roots[0].x = 0.0
            roots[0].y = 0.0

    @staticmethod
    def _node_width(node: DiagramNode) -> float:
        return max(node.w, 100.0)


def px(v: float) -> int:
    return int(round(v))
