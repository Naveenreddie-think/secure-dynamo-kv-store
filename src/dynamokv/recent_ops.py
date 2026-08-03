"""A small in-memory, bounded history of recent client operations, for the
Phase 10 dashboard's live operation-log feed. Single-process by
construction (matches GossipState/HintStore's same implicit assumption) --
run.py runs exactly one process per container, so this is fine as-is; it
would silently stop reflecting reality under `uvicorn --workers N`, which
nobody does here.

Populated by logging_middleware.py's RequestLogMiddleware (path-filtered
to actual client /v1/keys/* traffic only -- not internal replica fan-out,
not the dashboard's own polling of /v1/cluster-state); read by the
/v1/cluster-state route handler.
"""
import threading
from collections import deque
from typing import Any, Dict, List

_MAX_ENTRIES = 200
_lock = threading.Lock()
_entries: deque = deque(maxlen=_MAX_ENTRIES)


def record_operation(entry: Dict[str, Any]) -> None:
    with _lock:
        _entries.append(entry)


def recent_operations() -> List[Dict[str, Any]]:
    """A snapshot copy, most recent last. Guarded by the same lock as
    record_operation() -- routes.py's handlers run on a threadpool while
    RequestLogMiddleware appends from the event loop, so an unguarded
    `list(deque)` here can raise `RuntimeError: deque mutated during
    iteration` under real concurrent traffic, not just return stale data."""
    with _lock:
        return list(_entries)


def clear() -> None:
    """Test-only reset. In production this module is a genuine
    process-global singleton (one process = one node, never needs
    resetting) -- this exists solely so tests running in the same pytest
    process don't leak recorded operations into each other."""
    with _lock:
        _entries.clear()
