"""Fast, in-process proofs for the Phase 7 adversarial taxonomy's
categories that don't require real mTLS/sockets to demonstrate --
categories 4 (replay defense), 5 (clock forgery + self-heal), 6
(unauthorized delete), and 2's application-layer half (identity fields
accepted with no cross-check). The transport-dependent half of category 2,
plus categories 1/3/7/8/9/10, live in scripts/adversarial_testbed.py
against a real Docker Compose cluster -- see PROGRESS.md's Phase 7 entry
for the full taxonomy and why each category lands where it does.

Reuses test_node_quorum.py's mounted-transport multi-node cluster builder
rather than duplicating it.
"""
from dynamokv.vector_clock import VectorClock, Version
from tests.test_node_quorum import _build_cluster


def test_replay_of_old_write_is_dropped_by_reconcile():
    """Category 4 -- a working defense, proven live against the actual
    Node/reconcile() code path, not just vector_clock.py's own unit tests."""
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    resp1 = clients["node-1"].put("/keys/foo", json={"value": "v1"})
    clock1 = resp1.json()["clock"]
    clients["node-1"].put("/keys/foo", json={"value": "v2"})

    # attacker replays the captured OLD write directly at a replica's
    # internal primitive -- no client-facing route needed to attempt this
    apps["node-2"].state.node.put_local("foo", Version(value="v1", clock=VectorClock(clock1)))

    versions = apps["node-2"].state.node.get_local("foo")
    assert len(versions) == 1
    assert versions[0].value == "v2"


def test_clock_forgery_hijacks_the_key_immediately():
    """Category 5, part 1 -- a fabricated clock claimed for a node the
    attacker doesn't represent wins immediately and is served cluster-wide,
    since put_local()/reconcile() only compare clock magnitudes, never
    provenance."""
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    clients["node-1"].put("/keys/foo", json={"value": "legit"})

    poison = Version(value="poisoned", clock=VectorClock({"node-1": 999999}))
    for nid in node_ids:
        apps[nid].state.node.put_local("foo", poison)

    resp = clients["node-2"].get("/keys/foo")
    assert resp.status_code == 200
    assert resp.json()["value"] == "poisoned"


def test_clock_forgery_self_heals_on_next_real_write_by_impersonated_node():
    """Category 5, part 2 -- the hijack is not eternal: Node.put()'s
    merge(...).incremented(self.node_id) always dominates whatever it read,
    so the impersonated node's next real coordinated write naturally
    supersedes the poison. This is the self-heal boundary the report
    measures separately from the immediate-hijack finding."""
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    clients["node-1"].put("/keys/foo", json={"value": "legit"})
    poison = Version(value="poisoned", clock=VectorClock({"node-1": 999999}))
    for nid in node_ids:
        apps[nid].state.node.put_local("foo", poison)

    resp = clients["node-1"].put("/keys/foo", json={"value": "recovered"})
    assert resp.status_code == 200

    final = clients["node-1"].get("/keys/foo")
    assert final.status_code == 200
    assert final.json()["value"] == "recovered"


def test_delete_local_is_unconditional_regardless_of_clock():
    """Category 6 -- delete_local() has been clock-naive since Phase 4 by
    design; no vector-clock gate exists to defeat here at all."""
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    clients["node-1"].put("/keys/foo", json={"value": "bar"})

    for nid in node_ids:
        deleted = apps[nid].state.node.delete_local("foo")
        assert deleted is True

    resp = clients["node-1"].get("/keys/foo")
    assert resp.status_code == 404


def test_gossip_sender_field_is_never_validated():
    """Category 2 (application-layer half) -- handle_gossip() never reads
    or checks the `sender` field at all; any claimed sender is accepted
    identically. Provable without real mTLS since the gap is at the
    application layer, independent of transport identity."""
    node_ids = ["node-1", "node-2"]
    apps, clients = _build_cluster(node_ids, n=1, r=1, w=1)

    resp = clients["node-2"].post("/internal/gossip", json={"sender": "node-1", "table": {"node-2": 5}})
    assert resp.status_code == 200

    resp2 = clients["node-2"].post(
        "/internal/gossip", json={"sender": "totally-fabricated-identity", "table": {"node-2": 6}}
    )
    assert resp2.status_code == 200


def test_hint_target_field_is_never_validated_against_caller_identity():
    """Category 2 (application-layer half) -- add_hint()'s target_node_id
    is entirely client-supplied, with no check that the caller is who it
    claims to be forwarding a hint on behalf of, or that the target is even
    a real cluster member."""
    node_ids = ["node-1", "node-2"]
    apps, clients = _build_cluster(node_ids, n=1, r=1, w=1)

    resp = clients["node-2"].put(
        "/internal/hints/foo",
        json={"target": "some-other-node", "value": "bar", "clock": {"node-1": 1}},
    )
    assert resp.status_code == 200
    pending = apps["node-2"].state.node._hint_store.pending_for("some-other-node")
    assert "foo" in pending
