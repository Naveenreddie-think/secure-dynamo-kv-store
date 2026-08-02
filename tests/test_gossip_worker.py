import time

from dynamokv.gossip_worker import GossipWorker


class _CountingStub:
    def __init__(self):
        self.calls = 0

    def gossip_round(self):
        self.calls += 1


def test_worker_calls_gossip_round_periodically():
    stub = _CountingStub()
    worker = GossipWorker(stub, interval_seconds=0.05)
    worker.start()
    try:
        time.sleep(0.3)
    finally:
        worker.stop()
    assert stub.calls >= 3


def test_worker_stops_cleanly_and_thread_is_joined():
    stub = _CountingStub()
    worker = GossipWorker(stub, interval_seconds=0.05)
    worker.start()
    time.sleep(0.1)
    worker.stop()

    calls_after_stop = stub.calls
    time.sleep(0.2)
    assert stub.calls == calls_after_stop  # no more rounds ran after stop()
    assert worker._thread is None


def test_worker_survives_a_gossip_round_that_raises():
    class _RaisingStub:
        def __init__(self):
            self.calls = 0

        def gossip_round(self):
            self.calls += 1
            raise RuntimeError("boom")

    stub = _RaisingStub()
    worker = GossipWorker(stub, interval_seconds=0.05)
    worker.start()
    try:
        time.sleep(0.2)
    finally:
        worker.stop()
    assert stub.calls >= 2  # the loop kept going despite the exception


def test_start_is_idempotent():
    stub = _CountingStub()
    worker = GossipWorker(stub, interval_seconds=0.05)
    worker.start()
    first_thread = worker._thread
    worker.start()  # should be a no-op, not spawn a second thread
    assert worker._thread is first_thread
    worker.stop()
