import threading
from typing import Optional

from dynamokv.node import Node


class GossipWorker:
    """Runs Node.gossip_round() on a fixed interval in a background thread.

    Node itself never starts this -- it's constructed separately and
    started only via FastAPI's lifespan startup event (see main.py), so it
    never runs as a side effect of merely importing or constructing an app,
    which every pytest run already does once at import time.
    """

    def __init__(self, node: Node, interval_seconds: float) -> None:
        self._node = node
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self._node.gossip_round()
            except Exception:
                pass  # a single bad round should never kill the loop
