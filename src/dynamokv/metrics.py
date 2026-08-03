"""Prometheus /metrics -- served on its own dedicated, unpublished port
(config.METRICS_PORT), never per-request-authenticated: the network
boundary itself is the control here, the same load-bearing pattern
config.INTERNAL_PORT already uses (docker-compose.yml deliberately never
lists this port under a service's ports:, so it's unreachable from outside
the compose network by construction).

Separate from RequestLogMiddleware (logs) and AuditLogMiddleware (security
audit trail) -- this one only feeds Prometheus counters/histograms/gauges,
no logging side effect of its own.
"""
import time

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dynamokv.node import Node

REQUEST_COUNT = Counter(
    "dynamokv_requests_total", "Total requests handled", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "dynamokv_request_latency_seconds", "Request latency in seconds", ["method", "path"]
)
CLUSTER_NODE_UP = Gauge(
    "dynamokv_cluster_node_up", "1 if peer is believed up, 0 if believed down (per gossip)", ["node_id"]
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        latency = time.perf_counter() - start
        path = request.url.path
        REQUEST_COUNT.labels(method=request.method, path=path, status=str(response.status_code)).inc()
        REQUEST_LATENCY.labels(method=request.method, path=path).observe(latency)
        return response


def add_metrics_middleware(app: FastAPI) -> None:
    app.add_middleware(MetricsMiddleware)


def create_metrics_app(node: Node) -> FastAPI:
    """A third, minimal app -- just /metrics, no other routes. Cluster
    membership is computed fresh on each scrape (node.peer_ids() /
    node.down_peers()), not via a background updater -- there's no
    staleness window to reason about."""
    app = FastAPI(title="dynamokv (metrics)")

    @app.get("/metrics")
    def metrics() -> Response:
        down = node.down_peers()
        for peer_id in node.peer_ids():
            CLUSTER_NODE_UP.labels(node_id=peer_id).set(0 if peer_id in down else 1)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
