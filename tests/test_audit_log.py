import json

from fastapi.testclient import TestClient

from dynamokv.main import create_app
from dynamokv.storage.memory import MemoryStorage

AUTH_TOKENS = {
    "tok-orders": {"id": "client-a", "namespaces": {"orders": ["read", "write"]}},
}


def _read_entries(log_path):
    with open(log_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_allowed_request_is_logged(tmp_path):
    log_path = str(tmp_path / "audit.log")
    app = create_app(
        storage=MemoryStorage(), node_id="test-node", auth_tokens=AUTH_TOKENS, audit_log_path=log_path
    )
    client = TestClient(app)

    client.put("/keys/orders:1", json={"value": "bar"}, headers={"Authorization": "Bearer tok-orders"})

    entries = _read_entries(log_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["outcome"] == "allowed"
    assert entry["status_code"] == 200
    assert entry["client_id"] == "client-a"
    assert entry["method"] == "PUT"
    assert entry["key"] == "orders:1"
    assert entry["namespace"] == "orders"
    assert "timestamp" in entry


def test_denied_request_is_logged_without_leaking_the_raw_token(tmp_path):
    log_path = str(tmp_path / "audit.log")
    app = create_app(
        storage=MemoryStorage(), node_id="test-node", auth_tokens=AUTH_TOKENS, audit_log_path=log_path
    )
    client = TestClient(app)

    client.get("/keys/orders:1", headers={"Authorization": "Bearer not-a-real-token"})

    entries = _read_entries(log_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["outcome"] == "denied"
    assert entry["status_code"] == 401
    assert entry["client_id"] == "invalid"
    raw_log_text = open(log_path).read()
    assert "not-a-real-token" not in raw_log_text


def test_both_allowed_and_denied_appear_across_multiple_requests(tmp_path):
    log_path = str(tmp_path / "audit.log")
    app = create_app(
        storage=MemoryStorage(), node_id="test-node", auth_tokens=AUTH_TOKENS, audit_log_path=log_path
    )
    client = TestClient(app)

    client.put("/keys/orders:1", json={"value": "bar"}, headers={"Authorization": "Bearer tok-orders"})
    client.get("/keys/orders:1")  # no header -> 401
    client.get("/keys/orders:missing", headers={"Authorization": "Bearer tok-orders"})  # 404

    entries = _read_entries(log_path)
    assert len(entries) == 3
    outcomes = [e["outcome"] for e in entries]
    assert outcomes == ["allowed", "denied", "denied"]
    statuses = [e["status_code"] for e in entries]
    assert statuses == [200, 401, 404]
