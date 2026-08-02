from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


class VectorClock:
    """Tracks, per coordinating node, how many writes it has authored for a
    key. Comparing two clocks tells you whether one causally supersedes the
    other (dominates), they're identical (equal), or neither has knowledge
    of the other's writes (concurrent -- a genuine conflict).
    """

    def __init__(self, counters: Optional[Dict[str, int]] = None) -> None:
        self._counters: Dict[str, int] = dict(counters or {})

    @property
    def counters(self) -> Dict[str, int]:
        return dict(self._counters)

    def get(self, node_id: str) -> int:
        return self._counters.get(node_id, 0)

    def incremented(self, node_id: str) -> "VectorClock":
        new = dict(self._counters)
        new[node_id] = new.get(node_id, 0) + 1
        return VectorClock(new)

    def compare(self, other: "VectorClock") -> str:
        """Union of keys; a missing entry is treated as 0. Returns one of
        'equal', 'dominates', 'dominated', 'concurrent'."""
        keys = set(self._counters) | set(other._counters)
        self_ge = all(self.get(k) >= other.get(k) for k in keys)
        other_ge = all(other.get(k) >= self.get(k) for k in keys)
        if self_ge and other_ge:
            return "equal"
        if self_ge:
            return "dominates"
        if other_ge:
            return "dominated"
        return "concurrent"

    def dominates_or_equal(self, other: "VectorClock") -> bool:
        return self.compare(other) in ("dominates", "equal")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VectorClock) and self.compare(other) == "equal"

    def __repr__(self) -> str:
        return f"VectorClock({self._counters!r})"

    @staticmethod
    def merge(clocks: Iterable["VectorClock"]) -> "VectorClock":
        result: Dict[str, int] = {}
        for c in clocks:
            for k, v in c._counters.items():
                result[k] = max(result.get(k, 0), v)
        return VectorClock(result)


@dataclass(frozen=True)
class Version:
    value: Any
    clock: VectorClock

    def to_dict(self) -> dict:
        return {"value": self.value, "clock": self.clock.counters}

    @staticmethod
    def from_dict(d: dict) -> "Version":
        return Version(value=d["value"], clock=VectorClock(d["clock"]))


def reconcile(existing: List[Version], incoming: Version) -> List[Version]:
    """Merge one incoming version into a sibling list -- a replica's stored
    versions on write, or a running merge across replicas' responses on
    read. If incoming is dominated by (or identical to) something already
    present, it's stale or a duplicate retransmission -- drop it. Otherwise
    drop whatever incoming supersedes and add it. Applying this one version
    at a time, in any order, converges to the same maximal set: the true
    "no version here is superseded by another" antichain.
    """
    for v in existing:
        if v.clock.dominates_or_equal(incoming.clock):
            return existing
    survivors = [v for v in existing if incoming.clock.compare(v.clock) != "dominates"]
    survivors.append(incoming)
    return survivors
