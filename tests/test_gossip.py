from dynamokv.gossip import GossipState


class _FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_tick_advances_own_counter_and_timestamp():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    assert state.to_wire()["node-1"] == 0

    clock.advance(1.0)
    state.tick()
    assert state.to_wire()["node-1"] == 1


def test_merge_adopts_strictly_greater_counter():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 5})
    assert state.to_wire()["node-2"] == 5


def test_merge_ignores_stale_or_equal_counter():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 5})
    state.merge_wire({"node-2": 5})
    state.merge_wire({"node-2": 3})
    assert state.to_wire()["node-2"] == 5


def test_merge_never_regresses_local_timestamp_on_stale_incoming():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 5})

    clock.advance(100.0)  # node-2 goes quiet for a long time
    state.merge_wire({"node-2": 5})  # duplicate/stale info arrives

    # last_seen should NOT have been bumped by the stale re-announcement
    assert state.believed_down("node-2", now=clock.now, timeout=10.0) is True


def test_merge_never_lets_peer_override_own_counter():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    clock.advance(1.0)
    state.tick()  # node-1's own counter is now 1

    state.merge_wire({"node-1": 999})
    assert state.to_wire()["node-1"] == 1


def test_never_heard_from_peer_is_not_marked_down():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    clock.advance(1000.0)
    assert state.believed_down("node-99", now=clock.now, timeout=10.0) is False


def test_freshly_learned_peer_is_believed_up():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 1})
    assert state.believed_down("node-2", now=clock.now, timeout=10.0) is False


def test_believed_down_after_timeout_elapses():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 1})

    clock.advance(5.0)
    assert state.believed_down("node-2", now=clock.now, timeout=10.0) is False

    clock.advance(6.0)  # total 11s since last seen
    assert state.believed_down("node-2", now=clock.now, timeout=10.0) is True


def test_a_fresh_merge_resets_believed_down():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 1})

    clock.advance(20.0)
    assert state.believed_down("node-2", now=clock.now, timeout=10.0) is True

    state.merge_wire({"node-2": 2})  # node-2 checks back in
    assert state.believed_down("node-2", now=clock.now, timeout=10.0) is False


def test_down_nodes_returns_correct_set():
    clock = _FakeClock()
    state = GossipState("node-1", clock_fn=clock)
    state.merge_wire({"node-2": 1, "node-3": 1})

    clock.advance(20.0)
    state.merge_wire({"node-3": 2})  # node-3 checks back in, node-2 doesn't

    assert state.down_nodes(now=clock.now, timeout=10.0) == {"node-2"}
