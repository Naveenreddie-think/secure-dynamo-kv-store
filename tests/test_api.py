def test_put_then_get_round_trips(client):
    put_resp = client.put("/v1/keys/foo", json={"value": "bar"})
    assert put_resp.status_code == 200
    assert put_resp.json()["key"] == "foo"
    assert put_resp.json()["value"] == "bar"

    get_resp = client.get("/v1/keys/foo")
    assert get_resp.status_code == 200
    assert get_resp.json()["key"] == "foo"
    assert get_resp.json()["value"] == "bar"


def test_sequential_writes_never_spuriously_conflict(client):
    clocks = []
    for value in ("v1", "v2", "v3"):
        resp = client.put("/v1/keys/foo", json={"value": value})
        assert resp.status_code == 200
        clocks.append(resp.json()["clock"]["test-node"])

    # each write's own clock entry strictly increases
    assert clocks == sorted(clocks)
    assert len(set(clocks)) == len(clocks)

    get_resp = client.get("/v1/keys/foo")
    assert get_resp.status_code == 200
    assert get_resp.json()["value"] == "v3"


def test_get_missing_key_returns_404(client):
    resp = client.get("/v1/keys/missing")
    assert resp.status_code == 404


def test_put_overwrites_existing_value(client):
    client.put("/v1/keys/foo", json={"value": "bar"})
    resp = client.put("/v1/keys/foo", json={"value": "baz"})
    assert resp.status_code == 200
    assert client.get("/v1/keys/foo").json()["value"] == "baz"


def test_delete_existing_key_returns_200(client):
    client.put("/v1/keys/foo", json={"value": "bar"})
    resp = client.delete("/v1/keys/foo")
    assert resp.status_code == 200
    assert resp.json() == {"key": "foo", "deleted": True}
    assert client.get("/v1/keys/foo").status_code == 404


def test_delete_missing_key_returns_404(client):
    resp = client.delete("/v1/keys/missing")
    assert resp.status_code == 404


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["node_id"] == "test-node"


def test_arbitrary_json_value_round_trips(client):
    payload = {"nested": {"list": [1, 2, 3]}, "flag": True, "n": None}
    client.put("/v1/keys/complex", json={"value": payload})
    resp = client.get("/v1/keys/complex")
    assert resp.json()["value"] == payload
