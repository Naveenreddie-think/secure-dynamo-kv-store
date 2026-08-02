from typing import Any, Optional

from dynamokv.storage.base import StorageBackend


class Node:
    """A single node in the (eventually distributed) cluster.

    Currently a thin pass-through to its StorageBackend. This is deliberate:
    Phase 2 will give a Node awareness of the hash ring and the ability to
    route or forward a request to the node that actually owns a key, without
    changing the routes or storage layers that depend on this class today.
    """

    def __init__(self, node_id: str, storage: StorageBackend) -> None:
        self.node_id = node_id
        self._storage = storage

    def get(self, key: str) -> Optional[Any]:
        return self._storage.get(key)

    def put(self, key: str, value: Any) -> None:
        self._storage.put(key, value)

    def delete(self, key: str) -> bool:
        return self._storage.delete(key)

    def exists(self, key: str) -> bool:
        return self._storage.exists(key)
