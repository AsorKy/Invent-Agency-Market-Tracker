"""Communities and centrality for lens D2 (REQ-LNS-004).

``degree_centrality`` is pure Python (normalized by ``n - 1``).
``leiden_communities`` loads ``igraph`` and ``leidenalg`` lazily (``graph``
extra) with a fixed seed. Edges are undirected ``(a, b)`` pairs of node ids.
It relates to ``schedule.py``.
"""

from collections import defaultdict
from collections.abc import Sequence
from importlib import import_module

from libs.domain.topics import MissingDependencyError

SEED = 42
Edge = tuple[str, str]


def degree_centrality(edges: Sequence[Edge]) -> dict[str, float]:
    """Return degree / (n - 1) per node; duplicate edges and self loops are ignored."""
    neighbors: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        neighbors[a]
        neighbors[b]
        if a != b:
            neighbors[a].add(b)
            neighbors[b].add(a)
    n = len(neighbors)
    if n < 2:
        return {node: 0.0 for node in neighbors}
    return {node: len(adj) / (n - 1) for node, adj in sorted(neighbors.items())}


def leiden_communities(edges: Sequence[Edge], *, seed: int = SEED) -> dict[str, int]:
    """Return ``{node: community}`` using Leiden modularity optimization."""
    try:
        igraph = import_module("igraph")
        leidenalg = import_module("leidenalg")
    except ImportError as exc:
        raise MissingDependencyError(
            "igraph and leidenalg are not installed; install the 'graph' extra"
        ) from exc
    nodes = sorted({n for edge in edges for n in edge})
    index = {n: i for i, n in enumerate(nodes)}
    graph = igraph.Graph(n=len(nodes), edges=[(index[a], index[b]) for a, b in edges])
    part = leidenalg.find_partition(graph, leidenalg.ModularityVertexPartition, seed=seed)
    return {nodes[i]: int(c) for i, c in enumerate(part.membership)}
