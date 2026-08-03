"""Deterministic proof that a genuine network partition produces a real,
detected conflict -- not just a timing-dependent maybe.

With this project's default N=3/R=2/W=2, R+W > N guarantees any two writes'
replica sets overlap, so PUT's own read-before-write always sees prior
writes and a conflict can never actually happen (see config.py's own
W+R<=N warning). To construct one on purpose, this test deliberately uses a
5-node cluster with N=5/R=2/W=2 (R+W <= N) and a partition narrow enough
that two coordinators' writes provably can't overlap.

Reuses test_node_quorum.py's cluster-building/kill-node infrastructure
directly rather than duplicating it.
"""
import httpx
from fastapi.testclient import TestClient

from dynamokv.vector_clock import VectorClock, reconcile
from tests.test_node_quorum import _build_cluster, _kill_nodes, _peer_url, _RaisingTransport


def _restrict_reachable(apps, node_id, reachable_ids):
    """Rewire node_id's own outbound client so it can only successfully
    reach the given peer ids -- everything else raises, simulating a
    partition that isolates this node from those peers specifically
    (unlike _kill_nodes, which isolates a peer from everyone)."""
    node_ids = list(apps.keys())
    reachable_ids = set(reachable_ids)
    live_clients = {nid: TestClient(apps[nid], base_url=_peer_url(nid)) for nid in node_ids}
    mounts = {
        _peer_url(other_id): (
            live_clients[other_id]._transport if other_id in reachable_ids else _RaisingTransport()
        )
        for other_id in node_ids
        if other_id != node_id
    }
    apps[node_id].state.node._http = httpx.Client(mounts=mounts, timeout=5.0)


def test_partitioned_concurrent_writes_produce_a_detected_conflict():
    node_ids = [f"node-{i}" for i in range(1, 6)]
    apps, clients = _build_cluster(node_ids, n=5, r=2, w=2)

    # node-1 can only reach node-2; node-5 can only reach node-4; node-3 is
    # an uninvolved, fully isolated bystander.
    _restrict_reachable(apps, "node-1", ["node-2"])
    _restrict_reachable(apps, "node-5", ["node-4"])

    put_a = clients["node-1"].put("/v1/keys/foo", json={"value": "value-A"})
    assert put_a.status_code == 200
    clock_a = VectorClock(put_a.json()["clock"])

    put_b = clients["node-5"].put("/v1/keys/foo", json={"value": "value-B"})
    assert put_b.status_code == 200
    clock_b = VectorClock(put_b.json()["clock"])

    # neither write saw the other -- provably concurrent, not sequential
    assert clock_a.compare(clock_b) == "concurrent"

    # heal the partition
    _kill_nodes(apps, [])

    # A normal r=2 read after healing isn't guaranteed to span both
    # partition groups -- quorum reads return as soon as ANY 2 of 5 replicas
    # respond, so it could easily land on two replicas that only ever saw
    # one side. Gather every replica's own local versions directly instead,
    # which is what actually proves the conflict was preserved, not lost.
    merged = []
    for nid in node_ids:
        for v in apps[nid].state.node.get_local("foo"):
            merged = reconcile(merged, v)

    assert {v.value for v in merged} == {"value-A", "value-B"}
    assert len(merged) == 2
    a, b = merged
    assert a.clock.compare(b.clock) == "concurrent"
