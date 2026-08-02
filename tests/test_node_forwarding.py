"""Forwarding tests, entirely in-process -- no real sockets or Docker.

TestClient subclasses httpx.Client, and Node accepts an injectable
http_client. So a second app's TestClient can stand in for a real network
peer: when the coordinating Node "forwards" a request, it's actually
calling straight into the peer app's ASGI handling, exercising the same
request/response code path a real HTTP call would.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from dynamokv.main import create_app
from dynamokv.node import Node
from dynamokv.ring import HashRing
from dynamokv.storage.memory import MemoryStorage


def _make_ring_and_owner(nodes, key):
    ring = HashRing(nodes=nodes)
    return ring, ring.get_node(key)


def _coordinator_for(key, nodes=("node-1", "node-2")):
    """Build a coordinating Node whose owner for `key` is some other node,
    with that other node's TestClient wired in as the forwarding transport."""
    ring, owner = _make_ring_and_owner(list(nodes), key)
    coordinator_id = next(n for n in nodes if n != owner)

    peer_app = create_app(storage=MemoryStorage(), node_id=owner, cluster_nodes=list(nodes))
    peer_client = TestClient(peer_app, base_url=f"http://{owner}")

    coordinator = Node(
        node_id=coordinator_id,
        storage=MemoryStorage(),
        ring=ring,
        peers={owner: f"http://{owner}"},
        http_client=peer_client,
    )
    return coordinator, peer_client, owner


def test_put_forwards_to_owning_peer():
    coordinator, peer_client, owner = _coordinator_for("some-key")

    coordinator.put("some-key", "bar")

    assert peer_client.get("/keys/some-key").json()["value"] == "bar"


def test_get_forwards_to_owning_peer():
    coordinator, peer_client, owner = _coordinator_for("some-key")
    peer_client.put("/keys/some-key", json={"value": "bar"})

    assert coordinator.get("some-key") == "bar"


def test_get_missing_forwarded_key_returns_none():
    coordinator, peer_client, owner = _coordinator_for("missing-key")

    assert coordinator.get("missing-key") is None


def test_exists_forwards_to_owning_peer():
    coordinator, peer_client, owner = _coordinator_for("some-key")
    peer_client.put("/keys/some-key", json={"value": "bar"})

    assert coordinator.exists("some-key") is True
    assert coordinator.exists("does-not-exist") is False


def test_delete_forwards_to_owning_peer():
    coordinator, peer_client, owner = _coordinator_for("some-key")
    peer_client.put("/keys/some-key", json={"value": "bar"})

    assert coordinator.delete("some-key") is True
    assert peer_client.get("/keys/some-key").status_code == 404


def test_delete_missing_forwarded_key_returns_false():
    coordinator, peer_client, owner = _coordinator_for("missing-key")

    assert coordinator.delete("missing-key") is False


class _RaisingTransport(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("connection refused", request=request)


def test_unreachable_peer_raises_503():
    ring = HashRing(nodes=["node-1", "node-2"])
    owner = ring.get_node("some-key")
    coordinator_id = "node-1" if owner == "node-2" else "node-2"

    broken_client = httpx.Client(transport=_RaisingTransport())
    coordinator = Node(
        node_id=coordinator_id,
        storage=MemoryStorage(),
        ring=ring,
        peers={owner: f"http://{owner}"},
        http_client=broken_client,
    )

    with pytest.raises(Exception) as exc_info:
        coordinator.get("some-key")
    assert getattr(exc_info.value, "status_code", None) == 503
