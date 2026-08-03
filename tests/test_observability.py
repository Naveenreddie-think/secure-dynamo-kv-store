"""Phase 9 observability additions: structured request logging (distinct
from the security-focused audit log) and the /metrics endpoint. Fast,
in-process, no Docker/network needed -- mirrors this project's existing
convention of proving new mechanisms deterministically before/instead of
relying on a live run.
"""
import logging
import time

from fastapi.testclient import TestClient

from dynamokv.audit import AuditLogMiddleware
from dynamokv.main import create_app
from dynamokv.metrics import create_metrics_app
from dynamokv.node import Node
from dynamokv.storage.memory import MemoryStorage


class _FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _RecordCollector(logging.Handler):
    """Attached directly to the "dynamokv.access" logger. caplog can't be
    used here: RequestLogMiddleware deliberately sets propagate=False on
    that logger (see logging_middleware.py's docstring) so it never reaches
    the root logger/pytest's own capture ecosystem, which is exactly where
    caplog's handler lives."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _collect_access_logs(fn):
    logger = logging.getLogger("dynamokv.access")
    handler = _RecordCollector()
    logger.addHandler(handler)
    try:
        fn()
    finally:
        logger.removeHandler(handler)
    return handler.records


def test_structured_log_captures_node_method_path_latency_outcome():
    app = create_app(storage=MemoryStorage(), node_id="test-node")

    def make_request():
        with TestClient(app) as client:
            client.get("/healthz")

    records = _collect_access_logs(make_request)

    assert records, "expected at least one structured log entry"
    entry = records[-1].msg
    assert entry["node_id"] == "test-node"
    assert entry["method"] == "GET"
    assert entry["path"] == "/healthz"
    assert isinstance(entry["latency_ms"], float)
    assert entry["status_code"] == 200
    assert entry["outcome"] == "ok"


def test_structured_log_marks_error_outcome_on_4xx():
    app = create_app(storage=MemoryStorage(), node_id="test-node")

    def make_request():
        with TestClient(app) as client:
            client.get("/v1/keys/does-not-exist")

    records = _collect_access_logs(make_request)

    entry = records[-1].msg
    assert entry["status_code"] == 404
    assert entry["outcome"] == "error"


def test_structured_log_is_added_after_audit_middleware_and_observes_its_cost(monkeypatch, tmp_path):
    """Ordering matters: RequestLogMiddleware must wrap AuditLogMiddleware
    (added after it in main.py) so the reported latency includes the
    audit write itself, not hide it."""
    sleep_s = 0.05
    original_write_entry = AuditLogMiddleware._write_entry

    def slow_write_entry(self, request, status_code):
        time.sleep(sleep_s)
        return original_write_entry(self, request, status_code)

    monkeypatch.setattr(AuditLogMiddleware, "_write_entry", slow_write_entry)

    app = create_app(
        storage=MemoryStorage(),
        node_id="test-node",
        audit_log_path=str(tmp_path / "audit.log"),
    )

    def make_request():
        with TestClient(app) as client:
            client.get("/healthz")

    records = _collect_access_logs(make_request)

    entry = records[-1].msg
    assert entry["latency_ms"] >= sleep_s * 1000


def test_metrics_endpoint_returns_prometheus_text_with_expected_series():
    node = Node(node_id="node-1", storage=MemoryStorage())
    app = create_metrics_app(node)
    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "dynamokv_request_latency_seconds" in body
    assert "dynamokv_cluster_node_up" in body


def test_metrics_cluster_gauge_reflects_down_peers():
    clock = _FakeClock()
    node = Node(
        node_id="node-1",
        storage=MemoryStorage(),
        peers={"node-2": "http://fake:8000"},
        gossip_clock_fn=clock,
        gossip_failure_timeout=5.0,
    )
    node.handle_gossip({"node-2": 1})  # stamps node-2's last-seen time at clock()==0
    clock.advance(10)  # now well past the 5s failure timeout
    assert node.down_peers() == {"node-2"}

    app = create_metrics_app(node)
    with TestClient(app) as client:
        resp = client.get("/metrics")

    body = resp.text
    assert 'dynamokv_cluster_node_up{node_id="node-2"} 0.0' in body


def test_metrics_middleware_records_request_count_and_latency():
    app = create_app(storage=MemoryStorage(), node_id="test-node")
    with TestClient(app) as client:
        client.get("/healthz")

    metrics_app = create_metrics_app(Node(node_id="test-node", storage=MemoryStorage()))
    with TestClient(metrics_app) as client:
        resp = client.get("/metrics")

    body = resp.text
    assert 'dynamokv_requests_total{method="GET",path="/healthz",status="200"}' in body
