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


def test_adversarial_stale_relay_makes_a_healthy_node_appear_down():
    """Phase 7 taxonomy category 3's mechanism, proven deterministically:
    merge_wire only ever adopts a strictly-greater counter and there is no
    "this node is down" message in the wire protocol at all. A compromised
    relay that always reports the SAME frozen counter for a target --
    never propagating its true, advancing counter -- causes the victim's
    local timestamp for that target to go stale and eventually exceed the
    failure timeout, even though the target is genuinely healthy and its
    real counter kept ticking upward the entire time. Demonstrating this
    live against a real 3-node cluster additionally requires reshaping the
    topology (the default full mesh incidentally self-heals via random
    peer selection) -- see scripts/adversarial_testbed.py for that half.
    """
    clock = _FakeClock()
    victim = GossipState("node-1", clock_fn=clock)

    # first contact: the compromised relay reports node-3's counter as 5
    victim.merge_wire({"node-3": 5})

    real_node3_counter = 5
    for _ in range(5):
        clock.advance(2.0)  # one gossip interval
        real_node3_counter += 1  # node-3 is genuinely alive and ticking
        victim.merge_wire({"node-3": 5})  # relay withholds the real value

    assert real_node3_counter > 5  # node-3 really did keep advancing the whole time
    assert victim.believed_down("node-3", clock.now, timeout=6.0) is True
