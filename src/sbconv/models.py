"""Intermediate structures that a node carries from load to clean to render."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Node:
    """One node, in the shape the Clash file writes. The render stage reads `clash` by protocol."""

    name: str
    kind: str
    clash: dict
    dropped: str | None = None
    region: str | None = None
    plugin: str | None = None


@dataclass(slots=True)
class LoadResult:
    nodes: list[Node]
    # The Clash `hosts` table. An empty table means the file declares none.
    hosts: dict[str, str] = field(default_factory=dict)
