"""3-node quorum tests, entirely in-process -- no real sockets or Docker.

Extends the single-peer TestClient-as-http_client trick from
test_node_forwarding.py: since a Node's fan-out now needs to reach multiple
peers by URL from one client, each node's httpx.Client is built with
`mounts` mapping every peer's URL to an ASGITransport bound to that peer's
own app. Two-phase construction: build all apps first (each needs to
reference the others), then wire each one's client afterward.
"""
import httpx
from fastapi.testclient import TestClient

from dynamokv import config
from dynamokv.main import create_app
from dynamokv.storage.memory import MemoryStorage
from dynamokv.vector_clock import VectorClock, Version


class _RaisingTransport(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("connection refused", request=request)


def _peer_url(node_id):
    return f"http://{node_id}:{config.PORT}"


def _build_cluster(node_ids, n, r, w):
    node_ids = list(node_ids)
    apps = {
        nid: create_app(storage=MemoryStorage(), node_id=nid, cluster_nodes=node_ids, n=n, r=r, w=w)
        for nid in node_ids
    }
    clients = {nid: TestClient(app, base_url=_peer_url(nid)) for nid, app in apps.items()}

    # TestClient wraps a private but sync-capable ASGI transport
    # (_TestClientTransport). httpx.ASGITransport itself is async-only, so
    # it can't be mounted directly onto a sync httpx.Client -- this is why
    # Phase 2's single-peer test used a TestClient as http_client outright;
    # here we need to reach *multiple* peers from one client, so we mount
    # each peer TestClient's own transport under its URL instead.
    for nid, app in apps.items():
        mounts = {
            _peer_url(other_id): clients[other_id]._transport
            for other_id in node_ids
            if other_id != nid
        }
        app.state.node._http = httpx.Client(mounts=mounts, timeout=5.0)

    return apps, clients


def _kill_nodes(apps, dead_ids):
    """Make every surviving node's outbound calls to dead_ids fail, as if
    those nodes were unreachable -- without tearing down their own apps.
    Takes the full set of dead nodes at once (not incremental calls) since
    each call rebuilds mounts for all survivors from scratch."""
    dead_ids = set(dead_ids)
    node_ids = list(apps.keys())
    clients = {nid: TestClient(app, base_url=_peer_url(nid)) for nid, app in apps.items()}
    for nid, app in apps.items():
        if nid in dead_ids:
            continue
        mounts = {
            _peer_url(other_id): (
                _RaisingTransport() if other_id in dead_ids else clients[other_id]._transport
            )
            for other_id in node_ids
            if other_id != nid
        }
        app.state.node._http = httpx.Client(mounts=mounts, timeout=5.0)


def test_all_three_up_write_and_read_succeed_from_any_entry_point():
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    put_resp = clients["node-1"].put("/keys/foo", json={"value": "bar"})
    assert put_resp.status_code == 200

    for nid in node_ids:
        get_resp = clients[nid].get("/keys/foo")
        assert get_resp.status_code == 200
        assert get_resp.json()["value"] == "bar"

    # N=3 with a 3-node cluster: every replica physically has its own copy
    for nid in node_ids:
        assert apps[nid].state.node.exists_local("foo") is True


def test_one_of_three_down_write_and_read_still_succeed():
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)
    _kill_nodes(apps, ["node-3"])

    put_resp = clients["node-1"].put("/keys/foo", json={"value": "bar"})
    assert put_resp.status_code == 200

    get_resp = clients["node-1"].get("/keys/foo")
    assert get_resp.status_code == 200
    assert get_resp.json()["value"] == "bar"

    # the two live replicas both have it; the dead one never received it
    assert apps["node-1"].state.node.exists_local("foo") is True
    assert apps["node-2"].state.node.exists_local("foo") is True
    assert apps["node-3"].state.node.exists_local("foo") is False


def test_two_of_three_down_write_and_read_both_fail_503():
    node_ids = ["node-1", "node-2", "node-3"]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)
    _kill_nodes(apps, ["node-2", "node-3"])

    put_resp = clients["node-1"].put("/keys/foo", json={"value": "bar"})
    assert put_resp.status_code == 503

    # seed node-1's own local copy directly to isolate the read-quorum check
    apps["node-1"].state.node.put_local("foo", Version(value="bar", clock=VectorClock({"node-1": 1})))
    get_resp = clients["node-1"].get("/keys/foo")
    assert get_resp.status_code == 503


def test_coordinator_not_in_preference_list_still_works():
    node_ids = [f"node-{i}" for i in range(1, 6)]
    apps, clients = _build_cluster(node_ids, n=3, r=2, w=2)

    key = "some-key"
    ring = apps[node_ids[0]].state.node._ring
    prefs = ring.get_preference_list(key, 3)
    coordinator_id = next(nid for nid in node_ids if nid not in prefs)

    put_resp = clients[coordinator_id].put(f"/keys/{key}", json={"value": "bar"})
    assert put_resp.status_code == 200

    get_resp = clients[coordinator_id].get(f"/keys/{key}")
    assert get_resp.status_code == 200
    assert get_resp.json()["value"] == "bar"

    for nid in node_ids:
        expected = nid in prefs
        assert apps[nid].state.node.exists_local(key) is expected
