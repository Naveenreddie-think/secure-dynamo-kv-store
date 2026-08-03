import bisect
import hashlib
from typing import Dict, Iterable, List, Set, Tuple


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

    def get_preference_list(self, key: str, n: int) -> List[str]:
        """The n distinct real nodes responsible for replicas of this key,
        walking clockwise from the same starting point get_node uses (so
        result[0] always equals get_node(key)). Returns fewer than n if the
        cluster itself has fewer than n real nodes -- not an error."""
        if not self._sorted_hashes:
            raise RuntimeError("hash ring has no nodes")
        target = min(n, len(self._nodes))
        h = self._hash(key)
        start = bisect.bisect_left(self._sorted_hashes, h)
        if start == len(self._sorted_hashes):
            start = 0

        result: List[str] = []
        seen: Set[str] = set()
        total = len(self._sorted_hashes)
        for i in range(total):
            node_id = self._ring[self._sorted_hashes[(start + i) % total]]
            if node_id not in seen:
                seen.add(node_id)
                result.append(node_id)
                if len(result) == target:
                    break
        return result

    @property
    def nodes(self) -> Set[str]:
        return set(self._nodes)

    def sorted_points(self) -> List[Tuple[float, str]]:
        """Every virtual point as (position, owner), position = hash / 2**64
        in [0, 1) -- for dashboard ring visualization (Phase 10). Never
        returns the raw hash int: it's a 64-bit value, which exceeds
        JavaScript's 53-bit safe-integer range (IEEE754 doubles), so
        shipping it as a JSON number would silently lose precision in the
        browser (wrong sort order, apparent duplicates). Dividing by a
        fixed positive constant is monotonic, so iterating the
        already-sorted hash list preserves order for free -- no re-sort
        needed."""
        return [(h / 2**64, self._ring[h]) for h in self._sorted_hashes]
