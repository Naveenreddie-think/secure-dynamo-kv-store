"""Hinted handoff, built on the same in-process mounted-transport cluster
infrastructure test_node_quorum.py/test_node_conflicts.py already use.

Most tests use a 4-node cluster with N=3, so there's always exactly one
"spare" node beyond a key's normal preference list to serve as a hint
holder -- with a 3-node cluster and N=3, there's no room for one at all
(covered separately as the small-cluster edge case).
"""
from dynamokv.gossip import GossipState
from tests.test_node_quorum import _build_cluster, _kill_nodes


class _FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _prefs_and_hint_holder(apps, key, n):
    ring = next(iter(apps.values())).state.node._ring
    prefs = ring.get_preference_list(key, n)
    hint_candidates = ring.get_preference_list(key, n + 1)
    hint_holder = next((nid for nid in hint_candidates if nid not in prefs), None)
    return prefs, hint_holder


def _give_fake_clock(node, clock):
    node._gossip_clock_fn = clock
    node._gossip_state = GossipState(node.node_id, clock_fn=clock)


def test_reactive_hint_created_on_correct_holder_not_coordinator():
    node_ids = ["node-1", "node-2", "node-3", "node-4"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    key = "foo"
    prefs, hint_holder = _prefs_and_hint_holder(apps, key, 3)
    assert hint_holder is not None
    down_id = prefs[0]
    coordinator_id = next(nid for nid in prefs if nid != down_id)

    _kill_nodes(apps, [down_id])

    resp = clients[coordinator_id].put(f"/v1/keys/{key}", json={"value": "bar"})
    assert resp.status_code == 200  # W=2 met by the other two live prefs members

    # the hint landed on the hint-holder's own store, not the coordinator's
    assert apps[hint_holder].state.node._hint_store.has_pending(down_id) is True
    pending = apps[hint_holder].state.node._hint_store.pending_for(down_id)
    assert pending[key][0].value == "bar"
    if hint_holder != coordinator_id:
        assert apps[coordinator_id].state.node._hint_store.has_pending(down_id) is False


def test_proactively_presumed_down_replica_skips_live_attempt():
    node_ids = ["node-1", "node-2", "node-3", "node-4"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    key = "foo"
    prefs, hint_holder = _prefs_and_hint_holder(apps, key, 3)
    presumed_down_id = prefs[0]
    coordinator_id = next(nid for nid in prefs if nid != presumed_down_id)

    # Mark presumed_down_id as down in the COORDINATOR's own gossip view --
    # without actually breaking its transport. If put() incorrectly tried a
    # live write anyway, it would silently succeed; only a true proactive
    # skip leaves presumed_down_id's local storage empty after the write.
    clock = _FakeClock()
    coordinator_node = apps[coordinator_id].state.node
    _give_fake_clock(coordinator_node, clock)
    coordinator_node._gossip_state.merge_wire({presumed_down_id: 1})
    clock.advance(coordinator_node._gossip_failure_timeout + 1.0)

    resp = clients[coordinator_id].put(f"/v1/keys/{key}", json={"value": "bar"})
    assert resp.status_code == 200

    assert apps[presumed_down_id].state.node.exists_local(key) is False
    assert apps[hint_holder].state.node._hint_store.has_pending(presumed_down_id) is True


def test_hint_flushes_only_after_gossip_detects_recovery():
    node_ids = ["node-1", "node-2", "node-3", "node-4"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    key = "foo"
    prefs, hint_holder = _prefs_and_hint_holder(apps, key, 3)
    down_id = prefs[0]
    coordinator_id = next(nid for nid in prefs if nid != down_id)

    clock = _FakeClock()
    holder_node = apps[hint_holder].state.node
    _give_fake_clock(holder_node, clock)

    _kill_nodes(apps, [down_id])
    resp = clients[coordinator_id].put(f"/v1/keys/{key}", json={"value": "bar"})
    assert resp.status_code == 200
    assert holder_node._hint_store.has_pending(down_id) is True

    # holder learns down_id's heartbeat, then enough time passes with no
    # further contact that it's presumed down
    holder_node._gossip_state.merge_wire({down_id: 1})
    clock.advance(holder_node._gossip_failure_timeout + 1.0)
    assert holder_node._gossip_state.believed_down(down_id, clock.now, holder_node._gossip_failure_timeout) is True

    # a gossip round while still presumed down (and still actually
    # unreachable) must not deliver
    holder_node.gossip_round()
    assert holder_node._hint_store.has_pending(down_id) is True
    assert apps[down_id].state.node.exists_local(key) is False

    # heal the partition and let the holder learn down_id is back
    _kill_nodes(apps, [])
    holder_node._gossip_state.merge_wire({down_id: 2})
    assert holder_node._gossip_state.believed_down(down_id, clock.now, holder_node._gossip_failure_timeout) is False

    holder_node.gossip_round()
    assert apps[down_id].state.node.exists_local(key) is True
    assert holder_node._hint_store.has_pending(down_id) is False


def test_no_hint_holder_available_in_a_cluster_with_no_spare_node():
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)
    _kill_nodes(apps, ["node-3"])

    resp = clients["node-1"].put("/v1/keys/foo", json={"value": "bar"})
    assert resp.status_code == 200  # W=2 still met by node-1 + node-2, no crash

    assert apps["node-1"].state.node.exists_local("foo") is True
    assert apps["node-2"].state.node.exists_local("foo") is True
    assert apps["node-3"].state.node.exists_local("foo") is False
    for nid in node_ids:
        assert apps[nid].state.node._hint_store.has_pending("node-3") is False


def test_gossip_propagates_third_party_liveness_knowledge():
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=1, r=1, w=1)

    clock = _FakeClock()
    for nid in node_ids:
        _give_fake_clock(apps[nid].state.node, clock)

    # node-1 learns node-3's heartbeat directly, while node-3 is still up
    apps["node-1"].state.node._gossip_state.merge_wire({"node-3": 5})

    # node-3 goes dark -- node-2 never talks to it directly, only learns
    # about it by gossiping with node-1
    _kill_nodes(apps, ["node-3"])
    incoming = apps["node-1"].state.node._gossip_state.to_wire()
    apps["node-2"].state.node.handle_gossip(incoming)

    assert apps["node-2"].state.node._gossip_state.to_wire().get("node-3") == 5

    clock.advance(apps["node-2"].state.node._gossip_failure_timeout + 1.0)
    node2_state = apps["node-2"].state.node._gossip_state
    assert node2_state.believed_down("node-3", clock.now, apps["node-2"].state.node._gossip_failure_timeout) is True

    # gossip_round() itself still runs without error even with a dead peer
    # in the mix (random.choice may or may not pick it)
    apps["node-1"].state.node.gossip_round()
