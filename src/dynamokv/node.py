import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from dynamokv.gossip import GossipState
from dynamokv.hint_store import HintStore
from dynamokv.ring import HashRing
from dynamokv.storage.base import StorageBackend
from dynamokv.storage.memory import MemoryStorage
from dynamokv.vector_clock import VectorClock, Version, reconcile


class Node:
    """A single node in the cluster.

    Every read/write asks the ring for the key's N-node preference list,
    then fans the operation out concurrently to those N replicas (local
    storage for itself, an internal HTTP call for peers) and returns as
    soon as a write (W) or read (R) quorum of them succeed. A replica that
    fails to respond simply doesn't count toward the threshold.

    Each key can hold multiple sibling versions -- concurrent writes the
    system hasn't been able to causally order relative to each other.
    get()/put() reconcile whatever the queried replicas return via vector
    clocks: dominated versions are dropped, and if more than one truly
    concurrent version survives, that's a genuine conflict, surfaced to the
    caller rather than silently resolved.

    A background gossip round (driven externally, e.g. by a GossipWorker --
    Node itself never starts a thread) periodically exchanges heartbeat
    counters with a random peer, maintaining a local liveness view. When a
    write's target replica is presumed down (or fails live, right now),
    the coordinator hands the write to one extra "neighbor" node -- a
    hint -- instead of just dropping it, and that neighbor delivers it once
    gossip reports the real target is back. A hint never counts toward the
    write quorum; it's a pure availability side effect on failure.
    """

    def __init__(
        self,
        node_id: str,
        storage: StorageBackend,
        ring: Optional[HashRing] = None,
        peers: Optional[Dict[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
        n: int = 1,
        r: int = 1,
        w: int = 1,
        hint_storage: Optional[StorageBackend] = None,
        gossip_clock_fn: Callable[[], float] = time.monotonic,
        gossip_failure_timeout: float = 6.0,
    ) -> None:
        self.node_id = node_id
        self._storage = storage
        self._ring = ring if ring is not None else HashRing(nodes=[node_id])
        self._peers = peers or {}
        self._http = http_client if http_client is not None else httpx.Client(timeout=5.0)
        self.n = n
        self.r = r
        self.w = w
        self._executor = ThreadPoolExecutor(max_workers=max(n * 4, 8))

        self._gossip_clock_fn = gossip_clock_fn
        self._gossip_failure_timeout = gossip_failure_timeout
        self._gossip_state = GossipState(node_id, clock_fn=gossip_clock_fn)
        self._hint_store = HintStore(hint_storage if hint_storage is not None else MemoryStorage())

    # -- local primitives: no ring lookup, no fan-out, no forwarding. These
    # are what every replica's internal endpoint calls, and what a quorum op
    # falls back to when a preference-list entry is this node itself.

    def get_local(self, key: str) -> List[Version]:
        raw = self._storage.get(key)
        if raw is None:
            return []
        return [Version.from_dict(v) for v in raw]

    def put_local(self, key: str, version: Version) -> None:
        merged = reconcile(self.get_local(key), version)
        self._storage.put(key, [v.to_dict() for v in merged])

    def delete_local(self, key: str) -> bool:
        return self._storage.delete(key)

    def exists_local(self, key: str) -> bool:
        return self._storage.exists(key)

    def add_hint(self, target_node_id: str, key: str, version: Version) -> None:
        self._hint_store.add(target_node_id, key, version)

    # -- quorum machinery

    def _preference_list(self, key: str) -> List[str]:
        return self._ring.get_preference_list(key, self.n)

    def _peer_base_url(self, node_id: str) -> str:
        base_url = self._peers.get(node_id)
        if base_url is None:
            raise RuntimeError(f"no known address for node '{node_id}'")
        return base_url

    def _quorum_op(self, prefs: List[str], threshold: int, op_fn: Callable[[str], Any]) -> List[Any]:
        """Submit op_fn(node_id) for each node in prefs; return as soon as
        `threshold` of them succeed. op_fn signals failure by raising --
        any exception just means that replica doesn't count. Stragglers
        that haven't finished when threshold is met are left running; their
        eventual results are discarded."""
        futures = [self._executor.submit(op_fn, node_id) for node_id in prefs]
        successes: List[Any] = []
        for future in as_completed(futures):
            try:
                successes.append(future.result())
            except Exception:
                continue
            if len(successes) >= threshold:
                break
        return successes

    def _get_op(self, key: str) -> Callable[[str], List[Version]]:
        def op(node_id: str) -> List[Version]:
            if node_id == self.node_id:
                return self.get_local(key)
            base_url = self._peer_base_url(node_id)
            resp = self._http.get(f"{base_url}/internal/keys/{key}")
            if resp.status_code == 200:
                return [Version.from_dict(v) for v in resp.json()["versions"]]
            if resp.status_code == 404:
                return []
            raise RuntimeError(f"unexpected status {resp.status_code} from '{node_id}'")

        return op

    def _put_op(self, key: str, version: Version) -> Callable[[str], bool]:
        def op(node_id: str) -> bool:
            if node_id == self.node_id:
                self.put_local(key, version)
                return True
            base_url = self._peer_base_url(node_id)
            resp = self._http.put(
                f"{base_url}/internal/keys/{key}",
                json={"value": version.value, "clock": version.clock.counters},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"unexpected status {resp.status_code} from '{node_id}'")
            return True

        return op

    def _delete_op(self, key: str) -> Callable[[str], bool]:
        def op(node_id: str) -> bool:
            if node_id == self.node_id:
                return self.delete_local(key)
            base_url = self._peer_base_url(node_id)
            resp = self._http.delete(f"{base_url}/internal/keys/{key}")
            if resp.status_code not in (200, 404):
                raise RuntimeError(f"unexpected status {resp.status_code} from '{node_id}'")
            return resp.status_code == 200

        return op

    def _merged_read(self, prefs: List[str], threshold: int, key: str) -> List[Version]:
        results = self._quorum_op(prefs, threshold, self._get_op(key))
        if len(results) < threshold:
            raise HTTPException(status_code=503, detail=f"could not reach read quorum for key '{key}'")
        merged: List[Version] = []
        for version_list in results:
            for v in version_list:
                merged = reconcile(merged, v)
        return merged

    # -- gossip + hinted handoff

    def _partition_by_liveness(self, prefs: List[str]) -> Tuple[List[str], List[str]]:
        now = self._gossip_clock_fn()
        live: List[str] = []
        presumed_down: List[str] = []
        for node_id in prefs:
            if self._gossip_state.believed_down(node_id, now, self._gossip_failure_timeout):
                presumed_down.append(node_id)
            else:
                live.append(node_id)
        return live, presumed_down

    def _deliver_hint(self, hint_holder: str, target_node_id: str, key: str, version: Version) -> None:
        """Get `version` to `hint_holder`, to be held on behalf of
        `target_node_id` (the actual down replica) until it recovers.
        `hint_holder` and `target_node_id` are deliberately separate --
        who's holding the hint is not who it's for."""
        if hint_holder == self.node_id:
            self._hint_store.add(target_node_id, key, version)
            return
        try:
            base_url = self._peer_base_url(hint_holder)
            self._http.put(
                f"{base_url}/internal/hints/{key}",
                json={"target": target_node_id, "value": version.value, "clock": version.clock.counters},
            )
        except (RuntimeError, httpx.HTTPError):
            pass  # best-effort -- no further fallback candidate this phase

    def _flush_hints_for(self, node_id: str) -> None:
        pending = self._hint_store.pending_for(node_id)
        if not pending:
            return
        base_url = self._peer_base_url(node_id)
        for key, versions in pending.items():
            for version in versions:
                try:
                    resp = self._http.put(
                        f"{base_url}/internal/keys/{key}",
                        json={"value": version.value, "clock": version.clock.counters},
                    )
                    if resp.status_code == 200:
                        self._hint_store.clear_key(node_id, key)
                except httpx.HTTPError:
                    pass  # try again next gossip round

    def handle_gossip(self, incoming_table: Dict[str, int]) -> Dict[str, int]:
        self._gossip_state.merge_wire(incoming_table)
        return self._gossip_state.to_wire()

    def gossip_round(self) -> None:
        self._gossip_state.tick()
        if self._peers:
            peer_id = random.choice(list(self._peers))
            try:
                base_url = self._peer_base_url(peer_id)
                resp = self._http.post(
                    f"{base_url}/internal/gossip",
                    json={"sender": self.node_id, "table": self._gossip_state.to_wire()},
                )
                if resp.status_code == 200:
                    self._gossip_state.merge_wire(resp.json()["table"])
            except httpx.HTTPError:
                pass  # that peer's counter just won't advance in our view

        now = self._gossip_clock_fn()
        for peer_id in self._peers:
            if not self._gossip_state.believed_down(peer_id, now, self._gossip_failure_timeout):
                self._flush_hints_for(peer_id)

    # -- public API, used by routes.py

    def get(self, key: str) -> Version:
        prefs = self._preference_list(key)
        threshold = min(self.r, len(prefs))
        merged = self._merged_read(prefs, threshold, key)
        if not merged:
            raise HTTPException(status_code=404, detail=f"key '{key}' not found")
        if len(merged) == 1:
            return merged[0]
        raise HTTPException(
            status_code=409,
            detail={"key": key, "versions": [v.to_dict() for v in merged]},
        )

    def put(self, key: str, value: Any) -> Version:
        prefs = self._preference_list(key)
        read_threshold = min(self.r, len(prefs))
        merged = self._merged_read(prefs, read_threshold, key)
        new_clock = VectorClock.merge(v.clock for v in merged).incremented(self.node_id)
        new_version = Version(value=value, clock=new_clock)

        hint_candidates = self._ring.get_preference_list(key, len(prefs) + 1)
        hint_holder = next((nid for nid in hint_candidates if nid not in prefs), None)

        live, presumed_down = self._partition_by_liveness(prefs)
        if hint_holder is not None:
            for down_id in presumed_down:
                self._deliver_hint(hint_holder, down_id, key, new_version)

        def guarded_put_op(node_id: str) -> bool:
            try:
                return self._put_op(key, new_version)(node_id)
            except Exception:
                if hint_holder is not None and node_id != hint_holder:
                    self._deliver_hint(hint_holder, node_id, key, new_version)
                raise

        write_threshold = min(self.w, len(prefs))
        write_results = self._quorum_op(live, write_threshold, guarded_put_op)
        if len(write_results) < write_threshold:
            raise HTTPException(status_code=503, detail=f"could not reach write quorum for key '{key}'")
        return new_version

    def delete(self, key: str) -> bool:
        prefs = self._preference_list(key)
        threshold = min(self.w, len(prefs))
        results = self._quorum_op(prefs, threshold, self._delete_op(key))
        if len(results) < threshold:
            raise HTTPException(status_code=503, detail=f"could not reach write quorum for key '{key}'")
        return any(results)
