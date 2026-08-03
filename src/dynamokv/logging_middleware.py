import json
import logging
import sys
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dynamokv.recent_ops import record_operation

_LOGGER_NAME = "dynamokv.access"
_CLIENT_KEY_PREFIX = "/v1/keys/"


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.msg)


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    # Level/propagate are set unconditionally (not just the first time) --
    # if anything else (a test, another module) attaches a handler to this
    # logger before this runs, `logger.handlers` is already non-empty, and
    # gating the level-set on that same check would silently leave the
    # logger at its default NOTSET/root-inherited level, filtering out
    # every INFO record before any handler (ours or anyone else's) ever
    # sees it. Only the handler-attachment itself needs the once-only guard.
    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't also hand entries to the root logger / its ancestor-capturing handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonLineFormatter())
        logger.addHandler(handler)
    return logger


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Structured operational logging -- distinct from AuditLogMiddleware's
    security-focused audit trail (client_id/source_ip/allowed-denied,
    written to a dedicated per-node file for compliance-style retention).
    This captures node/operation/latency/outcome for EVERY request on
    EVERY app (public, internal -- including high-frequency gossip/hint
    traffic the audit log was never designed to absorb), as JSON Lines to
    stdout: 12-factor convention, captured by `docker compose logs`,
    swappable to a different sink later without touching middleware logic.
    Uses logging.getLogger rather than raw print()/file writes so it stays
    silent unless a handler is actually attached (no stdout spam during the
    pytest suite) and so tests can assert on it via caplog.

    Must be added AFTER add_audit_middleware() wherever both are present on
    the same app -- Starlette's middleware stack makes the last-added
    middleware outermost, so this only observes true end-to-end latency
    (including the audit middleware's own disk write) if it wraps the
    audit middleware, not the reverse.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._logger = _configure_logger()

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000
        node = getattr(request.app.state, "node", None)
        entry = {
            "timestamp": time.time(),
            "node_id": node.node_id if node is not None else None,
            "method": request.method,
            "path": request.url.path,
            "latency_ms": round(latency_ms, 3),
            "status_code": response.status_code,
            "outcome": "ok" if response.status_code < 400 else "error",
        }
        self._logger.info(entry)
        if request.url.path.startswith(_CLIENT_KEY_PREFIX):
            # Gated specifically to real client /v1/keys/* traffic -- not
            # /internal/keys/* (shares the "key" path param, but is replica
            # fan-out, not a client operation), and not /v1/cluster-state
            # itself (the dashboard's own polling would otherwise drown
            # real operations in noise). A 409 here only ever comes from
            # Node.get() -- Node.put() never raises it -- so "conflict"
            # only appears on GET entries, which is correct, not a bug.
            record_operation(
                {
                    **entry,
                    "key": request.path_params.get("key"),
                    "conflict": response.status_code == 409,
                }
            )
        return response


def add_request_log_middleware(app) -> None:
    app.add_middleware(RequestLogMiddleware)
