import bisect
import hashlib
from typing import Dict, Iterable, List, Set


class HashRing:
    """Consistent hash ring with virtual nodes.

    Each real node is given `virtual_nodes` points around the ring so that
    ownership is spread roughly evenly instead of in a few large arcs.
    Adding or removing a node only reassigns the keys between that node's
    own virtual points and its neighbors -- not the whole ring.

    Uses hashlib (not Python's built-in hash()) because hash() is randomized
    per-process via PYTHONHASHSEED. Two separate node processes must agree
    on where a key lands, or routing breaks silently the moment there's more
    than one process.
    """

    def __init__(self, nodes: Iterable[str] = (), virtual_nodes: int = 150) -> None:
        self.virtual_nodes = virtual_nodes
        self._ring: Dict[int, str] = {}
        self._sorted_hashes: List[int] = []
        self._nodes: Set[str] = set()
        for node_id in nodes:
            self.add_node(node_id)

    @staticmethod
    def _hash(s: str) -> int:
        digest = hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big")

    def add_node(self, node_id: str) -> None:
        if node_id in self._nodes:
            return
        self._nodes.add(node_id)
        for i in range(self.virtual_nodes):
            h = self._hash(f"{node_id}#{i}")
            self._ring[h] = node_id
            bisect.insort(self._sorted_hashes, h)

    def remove_node(self, node_id: str) -> None:
        if node_id not in self._nodes:
            return
        self._nodes.discard(node_id)
        for i in range(self.virtual_nodes):
            h = self._hash(f"{node_id}#{i}")
            del self._ring[h]
            idx = bisect.bisect_left(self._sorted_hashes, h)
            self._sorted_hashes.pop(idx)

    def get_node(self, key: str) -> str:
        if not self._sorted_hashes:
            raise RuntimeError("hash ring has no nodes")
        h = self._hash(key)
        # First virtual-node point >= h; bisect_left (not bisect_right) so an
        # exact match lands on that point's own node rather than the next one.
        idx = bisect.bisect_left(self._sorted_hashes, h)
        if idx == len(self._sorted_hashes):
            idx = 0
        return self._ring[self._sorted_hashes[idx]]

    @property
    def nodes(self) -> Set[str]:
        return set(self._nodes)
