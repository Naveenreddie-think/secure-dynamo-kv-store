from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from dynamokv.ring import HashRing
from dynamokv.storage.base import StorageBackend


class Node:
    """A single node in the cluster.

    Every read/write asks the ring for the key's N-node preference list,
    then fans the operation out concurrently to those N replicas (local
    storage for itself, an internal HTTP call for peers) and returns as
    soon as a write (W) or read (R) quorum of them succeed. A replica that
    fails to respond simply doesn't count toward the threshold -- there is
    no retry, hint, or repair here; that gap is Phase 5's job.
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

    # -- local primitives: no ring lookup, no fan-out, no forwarding. These
    # are what every replica's internal endpoint calls, and what a quorum op
    # falls back to when a preference-list entry is this node itself.

    def get_local(self, key: str) -> Optional[Any]:
        return self._storage.get(key)

    def put_local(self, key: str, value: Any) -> None:
        self._storage.put(key, value)

    def delete_local(self, key: str) -> bool:
        return self._storage.delete(key)

    def exists_local(self, key: str) -> bool:
        return self._storage.exists(key)

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

    def _put_op(self, key: str, value: Any) -> Callable[[str], bool]:
        def op(node_id: str) -> bool:
            if node_id == self.node_id:
                self.put_local(key, value)
                return True
            base_url = self._peer_base_url(node_id)
            resp = self._http.put(f"{base_url}/internal/keys/{key}", json={"value": value})
            if resp.status_code != 200:
                raise RuntimeError(f"unexpected status {resp.status_code} from '{node_id}'")
            return True

        return op

    def _get_op(self, key: str) -> Callable[[str], Tuple[bool, Optional[Any]]]:
        def op(node_id: str) -> Tuple[bool, Optional[Any]]:
            if node_id == self.node_id:
                found = self.exists_local(key)
                return (found, self.get_local(key) if found else None)
            base_url = self._peer_base_url(node_id)
            resp = self._http.get(f"{base_url}/internal/keys/{key}")
            if resp.status_code == 200:
                return (True, resp.json()["value"])
            if resp.status_code == 404:
                return (False, None)
            raise RuntimeError(f"unexpected status {resp.status_code} from '{node_id}'")

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

    # -- public API, used by routes.py

    def get(self, key: str) -> Optional[Any]:
        prefs = self._preference_list(key)
        threshold = min(self.r, len(prefs))
        results = self._quorum_op(prefs, threshold, self._get_op(key))
        if len(results) < threshold:
            raise HTTPException(status_code=503, detail=f"could not reach read quorum for key '{key}'")
        # presence beats absence: a write's W-ack can return before the
        # remaining N-W replicas catch up, so a concurrent read may see a
        # found/not-found split with no node failures involved at all.
        for found, value in results:
            if found:
                return value
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")

    def put(self, key: str, value: Any) -> None:
        prefs = self._preference_list(key)
        threshold = min(self.w, len(prefs))
        results = self._quorum_op(prefs, threshold, self._put_op(key, value))
        if len(results) < threshold:
            raise HTTPException(status_code=503, detail=f"could not reach write quorum for key '{key}'")

    def delete(self, key: str) -> bool:
        prefs = self._preference_list(key)
        threshold = min(self.w, len(prefs))
        results = self._quorum_op(prefs, threshold, self._delete_op(key))
        if len(results) < threshold:
            raise HTTPException(status_code=503, detail=f"could not reach write quorum for key '{key}'")
        return any(results)
