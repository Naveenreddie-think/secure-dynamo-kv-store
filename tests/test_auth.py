import pytest
from fastapi.testclient import TestClient

from dynamokv.main import create_app
from dynamokv.storage.memory import MemoryStorage

AUTH_TOKENS = {
    "tok-orders": {"id": "client-a", "namespaces": {"orders": ["read", "write"], "default": ["read"]}},
    "tok-readonly": {"id": "client-b", "namespaces": {"inventory": ["read"]}},
}


@pytest.fixture
def client():
    app = create_app(storage=MemoryStorage(), node_id="test-node", auth_tokens=AUTH_TOKENS)
    return TestClient(app)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_missing_header_is_401(client):
    resp = client.get("/keys/orders:1")
    assert resp.status_code == 401


def test_malformed_header_is_401(client):
    resp = client.get("/keys/orders:1", headers={"Authorization": "tok-orders"})
    assert resp.status_code == 401


def test_unknown_token_is_401(client):
    resp = client.get("/keys/orders:1", headers=_auth("not-a-real-token"))
    assert resp.status_code == 401


def test_valid_token_wrong_namespace_is_403(client):
    resp = client.get("/keys/inventory:1", headers=_auth("tok-orders"))
    assert resp.status_code == 403


def test_valid_token_wrong_verb_is_403(client):
    # tok-readonly only has "read" on inventory, not "write"
    resp = client.put("/keys/inventory:1", json={"value": "x"}, headers=_auth("tok-readonly"))
    assert resp.status_code == 403


def test_valid_token_correct_namespace_and_verb_succeeds(client):
    put_resp = client.put("/keys/orders:1", json={"value": "bar"}, headers=_auth("tok-orders"))
    assert put_resp.status_code == 200

    get_resp = client.get("/keys/orders:1", headers=_auth("tok-orders"))
    assert get_resp.status_code == 200
    assert get_resp.json()["value"] == "bar"


def test_default_namespace_used_when_no_colon_in_key(client):
    # "default" namespace grants tok-orders read but not write
    resp = client.get("/keys/plainkey", headers=_auth("tok-orders"))
    assert resp.status_code == 404  # allowed through auth, just doesn't exist

    resp = client.put("/keys/plainkey", json={"value": "x"}, headers=_auth("tok-orders"))
    assert resp.status_code == 403  # no write grant on default namespace


def test_healthz_does_not_require_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_auth_disabled_when_no_tokens_configured():
    app = create_app(storage=MemoryStorage(), node_id="test-node")  # no auth_tokens
    client = TestClient(app)
    resp = client.put("/keys/foo", json={"value": "bar"})
    assert resp.status_code == 200
