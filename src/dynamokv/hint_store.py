import threading
from typing import Dict, List

from dynamokv.storage.base import StorageBackend
from dynamokv.vector_clock import Version, reconcile


class HintStore:
    """Holds writes destined for a node that's currently unreachable, keyed
    by the intended *recipient* -- not by whoever happens to be holding
    them. Two different down nodes parking hints on the same neighbor never
    collide, since each target gets its own independent blob, flushed
    independently once its own liveness recovers.

    Uses its own dedicated StorageBackend instance, entirely separate from
    the one holding real application data -- a client PUTting a key that
    happens to collide with internal bookkeeping is not a risk worth
    carrying when a second backend instance avoids it for free.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage
        self._lock = threading.Lock()

    def add(self, target_node_id: str, key: str, version: Version) -> None:
        with self._lock:
            blob = self._load(target_node_id)
            existing = [Version.from_dict(v) for v in blob.get(key, [])]
            merged = reconcile(existing, version)
            blob[key] = [v.to_dict() for v in merged]
            self._save(target_node_id, blob)

    def pending_for(self, target_node_id: str) -> Dict[str, List[Version]]:
        blob = self._load(target_node_id)
        return {key: [Version.from_dict(v) for v in versions] for key, versions in blob.items()}

    def clear_key(self, target_node_id: str, key: str) -> None:
        with self._lock:
            blob = self._load(target_node_id)
            if key not in blob:
                return
            del blob[key]
            if blob:
                self._save(target_node_id, blob)
            else:
                self._storage.delete(target_node_id)

    def has_pending(self, target_node_id: str) -> bool:
        return self._storage.exists(target_node_id)

    def _load(self, target_node_id: str) -> dict:
        raw = self._storage.get(target_node_id)
        return raw if raw is not None else {}

    def _save(self, target_node_id: str, blob: dict) -> None:
        self._storage.put(target_node_id, blob)
