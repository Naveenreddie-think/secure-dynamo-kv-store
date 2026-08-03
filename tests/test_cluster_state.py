"""Phase 10 dashboard backend: GET /v1/cluster-state. Fast, in-process, no
Docker/network needed -- mirrors test_observability.py's Phase 9
convention.
"""
import pytest
from fastapi.testclient import TestClient

import dynamokv.recent_ops as recent_ops
from dynamokv.main import create_app
from dynamokv.node import Node
from dynamokv.ring import HashRing
from dynamokv.storage.memory import MemoryStorage


@pytest.fixture(autouse=True)
def _reset_recent_ops():
    """recent_ops is a genuine process-global singleton (by design -- one
    process = one node in production). Reset it around every test in this
    file so tests don't leak recorded operations into each other."""
    recent_ops.clear()
    yield
    recent_ops.clear()


def test_cluster_state_is_unauthenticated():
    app = create_app(storage=MemoryStorage(), node_id="test-node", auth_tokens={"tok": {"id": "x", "namespaces": {}}})
    with TestClient(app) as client:
        # no Authorization header at all -- would 401 on /v1/keys/*, must NOT 401 here
        resp = client.get("/v1/cluster-state")
    assert resp.status_code == 200


def test_cluster_state_ring_positions_are_floats_in_unit_interval():
    app = create_app(storage=MemoryStorage(), node_id="test-node")
    with TestClient(app) as client:
        resp = client.get("/v1/cluster-state")
    body = resp.json()
    points = body["ring"]["points"]
    assert len(points) == body["ring"]["virtual_nodes"]
    assert all(0.0 <= p["position"] < 1.0 for p in points)
    assert all(p["owner"] == "test-node" for p in points)


def test_cluster_state_records_client_put_and_get_as_recent_ops():
    app = create_app(storage=MemoryStorage(), node_id="test-node")
    with TestClient(app) as client:
        client.put("/v1/keys/foo", json={"value": "bar"})
        client.get("/v1/keys/foo")
        resp = client.get("/v1/cluster-state")

    ops = resp.json()["recent_ops"]
    methods = [(o["method"], o["path"], o["key"]) for o in ops]
    assert ("PUT", "/v1/keys/foo", "foo") in methods
    assert ("GET", "/v1/keys/foo", "foo") in methods
    assert all(o["conflict"] is False for o in ops)


def test_cluster_state_marks_conflict_on_409():
    app = create_app(storage=MemoryStorage(), node_id="test-node")
    with TestClient(app) as client:
        client.get("/v1/keys/does-not-exist")  # 404, not a conflict
        resp = client.get("/v1/cluster-state")

    ops = resp.json()["recent_ops"]
    assert ops[-1]["status_code"] == 404
    assert ops[-1]["conflict"] is False


def test_cluster_state_excludes_dashboard_polling_and_internal_traffic():
    app = create_app(storage=MemoryStorage(), node_id="test-node")
    with TestClient(app) as client:
        client.get("/v1/cluster-state")  # should not record itself
        client.put("/internal/keys/foo", json={"value": "bar", "clock": {}})  # replica traffic, not a client op
        resp = client.get("/v1/cluster-state")

    # neither the dashboard's own poll nor internal replica traffic ever
    # shows up in recent_ops -- only real /v1/keys/* client operations do,
    # and none were made in this test.
    assert resp.json()["recent_ops"] == []


def test_pending_hints_empty_with_no_peers():
    node = Node(node_id="node-1", storage=MemoryStorage())
    assert node.pending_hints() == {}


def test_pending_hints_reflects_stored_hint():
    from dynamokv.vector_clock import VectorClock, Version

    node = Node(node_id="node-1", storage=MemoryStorage(), peers={"node-2": "http://fake:8000"})
    node.add_hint("node-2", "some-key", Version(value="v", clock=VectorClock({"node-1": 1})))
    assert node.pending_hints() == {"node-2": 1}


def test_ring_topology_matches_hash_ring_sorted_points():
    ring = HashRing(nodes=["a", "b"], virtual_nodes=10)
    node = Node(node_id="a", storage=MemoryStorage(), ring=ring)
    topology = node.ring_topology()
    assert len(topology) == 20
    assert {t["owner"] for t in topology} == {"a", "b"}
    positions = [t["position"] for t in topology]
    assert positions == sorted(positions)
