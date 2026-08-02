from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from dynamokv.ring import HashRing
from dynamokv.storage.base import StorageBackend


class Node:
    """A single node in the cluster.

    Every method first asks the hash ring who owns the key. If it's this
    node, it behaves exactly as in Phase 1 (hit local storage). Otherwise it
    forwards the equivalent HTTP call to the owning peer's own /keys/{key}
    route and translates the response -- so from routes.py's point of view
    nothing changed: get/put/delete/exists still just work.
    """

    def __init__(
        self,
        node_id: str,
        storage: StorageBackend,
        ring: Optional[HashRing] = None,
        peers: Optional[Dict[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.node_id = node_id
        self._storage = storage
        self._ring = ring if ring is not None else HashRing(nodes=[node_id])
        self._peers = peers or {}
        self._http = http_client if http_client is not None else httpx.Client(timeout=5.0)

    def _owner(self, key: str) -> str:
        return self._ring.get_node(key)

    def _peer_base_url(self, owner: str) -> str:
        base_url = self._peers.get(owner)
        if base_url is None:
            raise HTTPException(status_code=503, detail=f"no known address for node '{owner}'")
        return base_url

    def get(self, key: str) -> Optional[Any]:
        owner = self._owner(key)
        if owner == self.node_id:
            return self._storage.get(key)
        base_url = self._peer_base_url(owner)
        try:
            resp = self._http.get(f"{base_url}/keys/{key}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"failed to reach node '{owner}': {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise HTTPException(status_code=503, detail=f"unexpected response from '{owner}': {resp.status_code}")
        return resp.json()["value"]

    def put(self, key: str, value: Any) -> None:
        owner = self._owner(key)
        if owner == self.node_id:
            self._storage.put(key, value)
            return
        base_url = self._peer_base_url(owner)
        try:
            resp = self._http.put(f"{base_url}/keys/{key}", json={"value": value})
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"failed to reach node '{owner}': {exc}") from exc
        if resp.status_code != 200:
            raise HTTPException(status_code=503, detail=f"unexpected response from '{owner}': {resp.status_code}")

    def delete(self, key: str) -> bool:
        owner = self._owner(key)
        if owner == self.node_id:
            return self._storage.delete(key)
        base_url = self._peer_base_url(owner)
        try:
            resp = self._http.delete(f"{base_url}/keys/{key}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"failed to reach node '{owner}': {exc}") from exc
        if resp.status_code not in (200, 404):
            raise HTTPException(status_code=503, detail=f"unexpected response from '{owner}': {resp.status_code}")
        return resp.status_code == 200

    def exists(self, key: str) -> bool:
        owner = self._owner(key)
        if owner == self.node_id:
            return self._storage.exists(key)
        base_url = self._peer_base_url(owner)
        try:
            resp = self._http.get(f"{base_url}/keys/{key}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"failed to reach node '{owner}': {exc}") from exc
        if resp.status_code not in (200, 404):
            raise HTTPException(status_code=503, detail=f"unexpected response from '{owner}': {resp.status_code}")
        return resp.status_code == 200
