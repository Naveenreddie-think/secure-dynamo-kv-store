import time
from typing import Callable, Dict, Set, Tuple


class GossipState:
    """Tracks, per node in the cluster roster, a heartbeat counter and the
    local time this node last saw that counter advance. Comparing "now"
    against that local timestamp is how a node decides a peer is probably
    down -- a soft, revisable suspicion, not a fact agreed cluster-wide.

    Only integer counters ever go out on the wire (see to_wire/merge_wire).
    Timestamps are always stamped locally, by the node doing the observing,
    so clock skew between processes/containers never enters the picture.
    """

    def __init__(self, node_id: str, clock_fn: Callable[[], float] = time.monotonic) -> None:
        self._node_id = node_id
        self._clock_fn = clock_fn
        now = clock_fn()
        self._table: Dict[str, Tuple[int, float]] = {node_id: (0, now)}

    def tick(self) -> None:
        """Advance this node's own counter -- proof of life for the next
        peer it gossips with."""
        counter, _ = self._table[self._node_id]
        self._table[self._node_id] = (counter + 1, self._clock_fn())

    def to_wire(self) -> Dict[str, int]:
        return {node_id: counter for node_id, (counter, _ts) in self._table.items()}

    def merge_wire(self, incoming: Dict[str, int]) -> None:
        now = self._clock_fn()
        for node_id, incoming_counter in incoming.items():
            if node_id == self._node_id:
                continue  # never let a peer's view of my own counter override it
            current = self._table.get(node_id)
            if current is None:
                # first time hearing about this node -- believed up as of now
                self._table[node_id] = (incoming_counter, now)
            elif incoming_counter > current[0]:
                self._table[node_id] = (incoming_counter, now)
            # incoming_counter <= current[0]: stale or duplicate info, ignore

    def believed_down(self, node_id: str, now: float, timeout: float) -> bool:
        if node_id == self._node_id:
            return False  # never consider self down
        entry = self._table.get(node_id)
        if entry is None:
            return False  # never heard of it -- no evidence either way
        _counter, last_seen = entry
        return (now - last_seen) > timeout

    def down_nodes(self, now: float, timeout: float) -> Set[str]:
        return {node_id for node_id in self._table if self.believed_down(node_id, now, timeout)}
